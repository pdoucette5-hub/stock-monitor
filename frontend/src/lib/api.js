const API_BASE =
  import.meta.env.VITE_API_BASE ?? (import.meta.env.PROD ? '' : 'http://127.0.0.1:8000')

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function request(path, options = {}) {
  let response

  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      response = await fetch(`${API_BASE}${path}`, {
        headers: {
          'Content-Type': 'application/json',
          ...options.headers,
        },
        ...options,
      })
      break
    } catch (err) {
      if (attempt === 1) {
        throw new Error(
          'Could not reach the stock-monitor server. Refresh the page and try again.',
          { cause: err },
        )
      }

      await sleep(500)
    }
  }

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

export function syncSheetTickers() {
  return request('/api/sheets/tickers/sync', {
    method: 'POST',
  })
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

export function fetchPortfolioPerformance(range = '1y', accounts = []) {
  const query = new URLSearchParams({ range })
  const selectedAccounts = (accounts || [])
    .map((account) => String(account).trim())
    .filter(Boolean)

  if (selectedAccounts.length > 0) {
    query.set('accounts', selectedAccounts.join(','))
  }

  return request(`/api/performance/portfolio?${query.toString()}`)
}

export function fetchPriceComparison(tickers, range = '1y', forceRefresh = false) {
  const cleaned = (tickers || [])
    .map((ticker) => String(ticker).trim().toUpperCase())
    .filter(Boolean)

  const query = new URLSearchParams({
    tickers: cleaned.join(','),
    range,
    force_refresh: forceRefresh ? 'true' : 'false',
  })

  return request(`/api/prices/compare?${query.toString()}`)
}

export function fetchChangeLog({ ticker = '', limit = 250 } = {}) {
  const query = new URLSearchParams({
    limit: String(limit),
  })

  if (ticker) {
    query.set('ticker', String(ticker).trim().toUpperCase())
  }

  return request(`/api/events?${query.toString()}`)
}
