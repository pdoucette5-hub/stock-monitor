import { Link } from 'react-router-dom'
import { actionBadgeClass, confidenceBadgeClass } from '../lib/actions'
import { formatCagrDecimal, formatMoney, formatNum } from '../lib/format'

const PORTFOLIO_COLUMNS = [
  { key: 'ticker', label: 'Ticker' },
  { key: 'shares', label: 'Shares', format: 'num' },
  { key: 'price', label: 'Price', format: 'money' },
  { key: 'market_value', label: 'Total Value', format: 'money' },
  { key: 'bear_price_y3', label: 'Bear Price (Y3)', format: 'money' },
  { key: 'base_price_y3', label: 'Base Price (Y3)', format: 'money' },
  { key: 'bull_price_y3', label: 'Bull Price (Y3)', format: 'money' },
  { key: 'bear_cagr_y3', label: 'Bear CAGR (3Y)', format: 'cagr' },
  { key: 'base_cagr_y3', label: 'Base CAGR (3Y)', format: 'cagr' },
  { key: 'bull_cagr_y3', label: 'Bull CAGR (3Y)', format: 'cagr' },
  { key: 'confidence', label: 'Confidence', format: 'confidence' },
  { key: 'action', label: 'Action', format: 'action' },
]

const WATCHLIST_COLUMNS = PORTFOLIO_COLUMNS.filter(
  (col) => !['shares', 'market_value'].includes(col.key),
)

export function getValuationColumns(mode) {
  return mode === 'portfolio' ? PORTFOLIO_COLUMNS : WATCHLIST_COLUMNS
}

export default function ValuationTable({
  rows,
  columns,
  loading,
  emptyMessage,
  lastUpdated,
  leadingColumn,
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
          <thead className="bg-slate-50">
            <tr>
              {leadingColumn && (
                <th className="px-4 py-3 font-semibold text-slate-700">
                  {leadingColumn.header}
                </th>
              )}
              {columns.map((col) => (
                <th
                  key={col.key}
                  className="whitespace-nowrap px-4 py-3 font-semibold text-slate-700"
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading && rows.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length + (leadingColumn ? 1 : 0)}
                  className="px-4 py-12 text-center text-slate-500"
                >
                  Loading…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length + (leadingColumn ? 1 : 0)}
                  className="px-4 py-12 text-center text-slate-500"
                >
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr
                  key={row.ticker}
                  className={[
                    'hover:bg-slate-50/80',
                    row.bottom_pinned ? 'bg-slate-50/50' : '',
                    row.show_in_holdings === false ? 'opacity-50' : '',
                  ].join(' ')}
                >
                  {leadingColumn && (
                    <td className="px-4 py-3">{leadingColumn.render(row)}</td>
                  )}
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className="whitespace-nowrap px-4 py-3 text-slate-700"
                    >
                      <Cell row={row} column={col} />
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {lastUpdated && !loading && (
        <p className="border-t border-slate-100 bg-slate-50 px-4 py-2 text-xs text-slate-500">
          Last updated {lastUpdated.toLocaleTimeString()} · Sorted: action rank, then
          weighted CAGR (pinned at bottom)
        </p>
      )}
    </div>
  )
}

function Cell({ row, column }) {
  if (column.render) {
    return column.render(row)
  }

  const value = row[column.key]

  if (column.key === 'ticker') {
    return (
      <Link
        to={`/stock?ticker=${encodeURIComponent(row.ticker)}`}
        className="font-medium text-blue-700 hover:text-blue-900 hover:underline"
      >
        {row.ticker}
      </Link>
    )
  }

  if (column.format === 'action') {
    return (
      <span
        className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${actionBadgeClass(value)}`}
      >
        {value ?? '—'}
      </span>
    )
  }

  if (column.format === 'confidence') {
    return (
      <span
        className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${confidenceBadgeClass(value)}`}
      >
        {value ?? '—'}
      </span>
    )
  }

  if (column.format === 'money') return formatMoney(value)
  if (column.format === 'num') return formatNum(value)
  if (column.format === 'cagr') return formatCagrDecimal(value)

  return value ?? '—'
}