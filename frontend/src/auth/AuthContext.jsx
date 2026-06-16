import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { fetchAuthConfig, fetchAuthUser, setAuthToken } from '../lib/api'

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

export function AuthProvider({ children }) {
  const [config, setConfig] = useState({ enabled: false, client_id: '' })
  const [loading, setLoading] = useState(true)
  const [token, setToken] = useState('')
  const [user, setUser] = useState(null)
  const [error, setError] = useState('')
  const signInButtonRef = useRef(null)

  useEffect(() => {
    let cancelled = false

    async function loadConfig() {
      setLoading(true)
      setError('')
      try {
        const payload = await fetchAuthConfig()
        if (!cancelled) setConfig(payload || { enabled: false, client_id: '' })
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
  }, [])

  useEffect(() => {
    if (!config.enabled || !config.client_id || token) return undefined

    let cancelled = false

    async function prepareGoogleSignIn() {
      try {
        await loadGoogleScript()
        if (cancelled) return

        window.google.accounts.id.initialize({
          client_id: config.client_id,
          callback: async (response) => {
            const credential = response?.credential || ''
            const claims = decodeJwtPayload(credential)
            setToken(credential)
            setAuthToken(credential)
            try {
              const profile = await fetchAuthUser()
              setUser({
                email: profile?.email || claims?.email || '',
                name: profile?.name || claims?.name || claims?.email || '',
                picture: profile?.picture || claims?.picture || '',
                role: profile?.role || 'full',
              })
            } catch {
              setUser({
                email: claims?.email || '',
                name: claims?.name || claims?.email || '',
                picture: claims?.picture || '',
                role: 'limited',
              })
            }
          },
        })

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
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to initialize Google sign-in')
        }
      }
    }

    prepareGoogleSignIn()
    return () => {
      cancelled = true
    }
  }, [config, token])

  const value = useMemo(
    () => ({
      authEnabled: Boolean(config.enabled),
      loading,
      token,
      user,
      signOut: () => {
        setToken('')
        setUser(null)
        setAuthToken('')
        if (window.google?.accounts?.id) {
          window.google.accounts.id.disableAutoSelect()
        }
      },
    }),
    [config.enabled, loading, token, user],
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
