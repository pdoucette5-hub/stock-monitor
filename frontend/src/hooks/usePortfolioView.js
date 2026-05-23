import { useCallback, useEffect, useState } from 'react'
import { fetchPortfolioView } from '../lib/api'

export function usePortfolioView() {
  const [view, setView] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)

  const load = useCallback(async (forceRefresh = false) => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchPortfolioView(forceRefresh)
      setView(data)
      setLastUpdated(new Date())
      return data
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data')
      setView(null)
      throw err
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return { view, setView, loading, error, lastUpdated, load }
}
