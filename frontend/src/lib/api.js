const API_BASE = import.meta.env.PROD ? '' : 'http://127.0.0.1:8000'

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  })

  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `Request failed: ${response.status}`)
  }

  if (response.status === 204) return null
  return response.json()
}

export function fetchPortfolioScenarios() {
  return request('/api/portfolio')
}

export function fetchPortfolioView(forceRefresh = false) {
  const query = forceRefresh ? '?force_refresh=true' : ''
  return request(`/api/portfolio/view${query}`)
}

export function savePortfolioControls(updates, forceRefresh = false) {
  const query = forceRefresh ? '?force_refresh=true' : ''
  return request(`/api/portfolio/controls${query}`, {
    method: 'PUT',
    body: JSON.stringify({ updates }),
  })
}

export function savePortfolioShares(ticker, shares, forceRefresh = false) {
  const query = forceRefresh ? '?force_refresh=true' : ''
  return request(`/api/portfolio/shares${query}`, {
    method: 'PUT',
    body: JSON.stringify({ ticker, shares }),
  })
}

export function fetchTickersConfig() {
  return request('/api/config/tickers')
}

export function fetchTickerRegistry() {
  return request('/api/tickers')
}

export function addPortfolioTicker(ticker, shares) {
  return request('/api/tickers/portfolio', {
    method: 'PUT',
    body: JSON.stringify({ ticker, shares }),
  })
}

export function addWatchlistTicker(ticker) {
  return request('/api/tickers/watchlist', {
    method: 'PUT',
    body: JSON.stringify({ ticker }),
  })
}

export function archiveTicker(ticker) {
  return request('/api/tickers/archive', {
    method: 'PUT',
    body: JSON.stringify({ ticker }),
  })
}

export function restoreTicker(ticker, list) {
  return request('/api/tickers/restore', {
    method: 'PUT',
    body: JSON.stringify({ ticker, list }),
  })
}

export function removeTicker(ticker) {
  return request(`/api/tickers/${encodeURIComponent(String(ticker).trim().toUpperCase())}`, {
    method: 'DELETE',
  })
}

export function fetchGlobalSettings() {
  return request('/api/settings')
}

export function fetchStockScenario(ticker) {
  const normalized = encodeURIComponent(String(ticker).trim().toUpperCase())
  return request(`/api/stock/${normalized}`)
}

export function saveStockScenario(ticker, payload) {
  const normalized = encodeURIComponent(String(ticker).trim().toUpperCase())
  return request(`/api/stock/${normalized}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function fetchTransactions(ticker) {
  const normalized = encodeURIComponent(String(ticker).trim().toUpperCase())
  return request(`/api/transactions/${normalized}`)
}

export function createTransaction(ticker, payload) {
  const normalized = encodeURIComponent(String(ticker).trim().toUpperCase())
  return request(`/api/transactions/${normalized}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateTransaction(ticker, transactionId, payload) {
  const normalized = encodeURIComponent(String(ticker).trim().toUpperCase())
  return request(`/api/transactions/${normalized}/${encodeURIComponent(transactionId)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deleteTransaction(ticker, transactionId) {
  const normalized = encodeURIComponent(String(ticker).trim().toUpperCase())
  return request(`/api/transactions/${normalized}/${encodeURIComponent(transactionId)}`, {
    method: 'DELETE',
  })
}

export function fetchPositionSummary(ticker) {
  const normalized = encodeURIComponent(String(ticker).trim().toUpperCase())
  return request(`/api/position/${normalized}`)
}

export function fetchPriceHistory(ticker, range = '1y', forceRefresh = false) {
  const normalized = encodeURIComponent(String(ticker).trim().toUpperCase())
  const query = new URLSearchParams({
    ticker: normalized,
    range,
    force_refresh: forceRefresh ? 'true' : 'false',
  })
  return request(`/api/prices/history?${query.toString()}`)
}

export function fetchPortfolioPerformance(range = '1y') {
  const query = new URLSearchParams({ range })
  return request(`/api/performance/portfolio?${query.toString()}`)
}