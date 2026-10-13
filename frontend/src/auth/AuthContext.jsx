import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { fetchAuthConfig, fetchAuthUser, onAuthExpired, setAuthToken } from '../lib/api'

const GOOGLE_SCRIPT_SRC = 'https://accounts.google.com/gsi/client'

const AuthContext = createContext({
  authEnabled: false,
  loading: true,
  user: null,
  token: '',
  signOut: () => {},
})

function decodeJwtPayload(token) {
  try {
    const [, payload] = String(token || '').split('.')
    if (!payload) return null
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(window.atob(normalized))
  } catch {
    return null
  }
}

function loadGoogleScript() {
  return new Promise((resolve, reject) => {
    if (window.google?.accounts?.id) {
      resolve()
      return
    }

    const existing = document.querySelector(`script[src="${GOOGLE_SCRIPT_SRC}"]`)
    if (existing) {
      existing.addEventListener('load', resolve, { once: true })
      existing.addEventListener('error', reject, { once: true })
      return
    }

    const script = document.createElement('script')
    script.src = GOOGLE_SCRIPT_SRC
    script.async = true
    script.defer = true
    script.onload = resolve
    script.onerror = reject
    document.head.appendChild(script)
  })
}

// The Google ID token is kept for this browser tab only (sessionStorage), so
// reloading does not ask to sign in again. It is cleared when the tab closes,
// on sign-out, and when it expires.
const TOKEN_STORAGE_KEY = 'stock-monitor.google-id-token'
// Renew this long before the token expires (Google ID tokens last one hour).
const RENEW_BEFORE_EXPIRY_MS = 5 * 60 * 1000
// Ignore a stored token with less than this much time left.
const MIN_REMAINING_MS = 60 * 1000

function tokenExpiresAt(token) {
  const exp = Number(decodeJwtPayload(token)?.exp)
  return Number.isFinite(exp) ? exp * 1000 : 0
}

function readStoredToken() {
  try {
    const token = window.sessionStorage.getItem(TOKEN_STORAGE_KEY) || ''
    return tokenExpiresAt(token) - Date.now() > MIN_REMAINING_MS ? token : ''
  } catch {
    return ''
  }
}

function storeToken(token) {
  try {
    if (token) window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token)
    else window.sessionStorage.removeItem(TOKEN_STORAGE_KEY)
  } catch {
    // Storage can be unavailable (private mode); sign-in still works per page load.
  }
}

export function AuthProvider({ children }) {
  const [config, setConfig] = useState({ enabled: false, client_id: '' })
  const [loading, setLoading] = useState(true)
  const [token, setToken] = useState('')
  const [user, setUser] = useState(null)
  const [error, setError] = useState('')
  const [googleReady, setGoogleReady] = useState(false)
  const signInButtonRef = useRef(null)
  // After an explicit sign-out, wait for a click instead of signing back in.
  const signedOutRef = useRef(false)

  const clearSession = useCallback(() => {
    setToken('')
    setUser(null)
    setAuthToken('')
    storeToken('')
  }, [])

  // Accept a Google ID token: from the sign-in button, automatic sign-in,
  // renewal, or this tab's stored session.
  const acceptCredential = useCallback(
    async (credential) => {
      const claims = decodeJwtPayload(credential)
      signedOutRef.current = false
      setToken(credential)
      setAuthToken(credential)
      storeToken(credential)
      try {
        const profile = await fetchAuthUser()
        setUser({
          email: profile?.email || claims?.email || '',
          name: profile?.name || claims?.name || claims?.email || '',
          picture: profile?.picture || claims?.picture || '',
          role: profile?.role || 'full',
        })
        setError('')
      } catch (err) {
        clearSession()
        setError(err instanceof Error ? err.message : 'Sign-in is not allowed')
      }
    },
    [clearSession],
  )

  useEffect(() => {
    let cancelled = false

    async function loadConfig() {
      setLoading(true)
      setError('')
      try {
        const payload = await fetchAuthConfig()
        if (cancelled) return
        const nextConfig = payload || { enabled: false, client_id: '' }
        setConfig(nextConfig)
        const stored = nextConfig.enabled ? readStoredToken() : ''
        if (stored) await acceptCredential(stored)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load sign-in settings')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadConfig()
    return () => {
      cancelled = true
    }
  }, [acceptCredential])

  // A rejected sign-in (401) returns to the sign-in screen instead of leaving
  // the page with empty data.
  useEffect(() => {
    onAuthExpired(() => clearSession())
    return () => onAuthExpired(null)
  }, [clearSession])

  // Load and initialize Google sign-in once. auto_select signs a returning
  // user straight back in without a click when Google allows it.
  useEffect(() => {
    if (!config.enabled || !config.client_id) return undefined

    let cancelled = false

    async function initializeGoogle() {
      try {
        await loadGoogleScript()
        if (cancelled) return
        window.google.accounts.id.initialize({
          client_id: config.client_id,
          auto_select: true,
          callback: (response) => {
            const credential = response?.credential || ''
            if (credential) acceptCredential(credential)
          },
        })
        setGoogleReady(true)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to initialize Google sign-in')
        }
      }
    }

    initializeGoogle()
    return () => {
      cancelled = true
    }
  }, [config, acceptCredential])

  // Signed out: show the button and offer automatic sign-in.
  useEffect(() => {
    if (!googleReady || token || loading) return
    if (signInButtonRef.current) {
      signInButtonRef.current.innerHTML = ''
      window.google.accounts.id.renderButton(signInButtonRef.current, {
        theme: 'outline',
        size: 'large',
        type: 'standard',
        text: 'signin_with',
        shape: 'rectangular',
      })
    }
    if (!signedOutRef.current) window.google.accounts.id.prompt()
  }, [googleReady, token, loading])

  // Signed in: renew shortly before the token expires, and sign out cleanly
  // if renewal did not happen by the time it does.
  useEffect(() => {
    if (!token) return undefined
    const expiresAt = tokenExpiresAt(token)
    if (!expiresAt) return undefined
    const now = Date.now()
    const renewTimer = setTimeout(() => {
      if (window.google?.accounts?.id) window.google.accounts.id.prompt()
    }, Math.max(0, expiresAt - RENEW_BEFORE_EXPIRY_MS - now))
    const expireTimer = setTimeout(() => clearSession(), Math.max(0, expiresAt - now))
    return () => {
      clearTimeout(renewTimer)
      clearTimeout(expireTimer)
    }
  }, [token, clearSession])

  const value = useMemo(
    () => ({
      authEnabled: Boolean(config.enabled),
      loading,
      token,
      user,
      signOut: () => {
        signedOutRef.current = true
        clearSession()
        if (window.google?.accounts?.id) {
          window.google.accounts.id.disableAutoSelect()
        }
      },
    }),
    [config.enabled, loading, token, user, clearSession],
  )

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-600">
        Loading sign-in settings...
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
        <div className="max-w-md rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-800">
          {error}
        </div>
      </div>
    )
  }

  if (config.enabled && !token) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
        <div className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 text-center shadow-sm">
          <h1 className="text-2xl font-semibold text-slate-900">Stock Monitor</h1>
          <p className="mt-2 text-sm text-slate-600">
            Sign in with an approved Google account to view private portfolio data.
          </p>
          {!config.client_id ? (
            <div className="mt-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              Google sign-in is enabled, but no client ID is configured.
            </div>
          ) : (
            <div className="mt-6 flex justify-center" ref={signInButtonRef} />
          )}
        </div>
      </div>
    )
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  return useContext(AuthContext)
}
