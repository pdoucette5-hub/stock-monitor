import { useCallback, useEffect, useState } from 'react'
import { fetchPortfolioView } from '../lib/api'

const CACHE_TTL_MS = 10 * 60 * 1000

let cachedView = null
let cachedAt = null
let inFlightLoad = null

export function clearPortfolioViewCache() {
  cachedView = null
  cachedAt = null
  inFlightLoad = null
}

function isCacheFresh() {
  return cachedView && cachedAt && Date.now() - cachedAt.getTime() < CACHE_TTL_MS
}

export function usePortfolioView() {
  const [view, setView] = useState(cachedView)
  const [loading, setLoading] = useState(!cachedView)
  const [error, setError] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(cachedAt)

  const load = useCallback(async (forceRefresh = false, options = {}) => {
    if (options.preferCache && !forceRefresh && cachedView) {
      setView(cachedView)
      setLastUpdated(cachedAt)
      setLoading(false)
      setError(null)

      if (isCacheFresh()) {
        return cachedView
      }
    }

    if (!forceRefresh && inFlightLoad) {
      setLoading(!cachedView)
      return inFlightLoad
    }

    setLoading(!cachedView)
    setError(null)

    const request = fetchPortfolioView(forceRefresh)
    if (!forceRefresh) inFlightLoad = request

    try {
      const data = await request
      cachedView = data
      cachedAt = new Date()
      setView(data)
      setLastUpdated(cachedAt)
      return data
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data')
      if (!cachedView) setView(null)
      throw err
    } finally {
      if (inFlightLoad === request) inFlightLoad = null
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load(false, { preferCache: true })
  }, [load])

  return { view, setView, loading, error, lastUpdated, load }
}
