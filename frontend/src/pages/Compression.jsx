import { Link } from 'react-router-dom'
import PageToolbar from '../components/PageToolbar'
import { usePortfolioView } from '../hooks/usePortfolioView'
import { formatCagrDecimal, formatMoney } from '../lib/format'

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${(Number(value) * 100).toFixed(1)}%`
}

function formatMultiple(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toFixed(1)}x`
}

function scoreClass(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return 'bg-slate-100 text-slate-600'
  }
  if (value >= 0.35) return 'bg-emerald-100 text-emerald-800'
  if (value >= 0.18) return 'bg-lime-100 text-lime-800'
  if (value >= 0.05) return 'bg-amber-100 text-amber-800'
  return 'bg-slate-100 text-slate-600'
}

function explainRow(row) {
  if (row.earnings_growth_1y == null || row.price_return_1y == null) {
    return 'Needs positive current/prior earnings and one year of price history.'
  }
  if (row.revenue_growth_1y != null && row.revenue_growth_1y < 0) {
    return 'Earnings outpaced price, but revenue declined.'
  }
  if (row.multiple_change_1y != null && row.multiple_change_1y < 0) {
    return 'Earnings grew faster than price, compressing the multiple.'
  }
  return 'No meaningful compression signal.'
}

export default function Compression() {
  const { view, loading, error, lastUpdated, load } = usePortfolioView()
  const rows = [...(view?.portfolio ?? [])]
    .filter((row) => row.show_in_holdings !== false)
    .sort((a, b) => {
      const left = Number(a.compression_opportunity_score ?? -Infinity)
      const right = Number(b.compression_opportunity_score ?? -Infinity)
      return right - left
    })

  return (
    <div className="mx-auto w-[98vw] px-4 py-8">
      <PageToolbar
        title="Compression"
        description="Price versus earnings decomposition for spotting possible unjustified multiple compression."
        loading={loading}
        onReload={load}
        onUpdatePrices={load}
      />

      {error && (
        <div
          role="alert"
          className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-3 font-semibold text-slate-700">Ticker</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Price Return</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Earnings Growth</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Multiple Change</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Opportunity</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Current P/E</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Prior P/E</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Revenue Growth</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Base CAGR</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Read</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading && rows.length === 0 ? (
                <tr>
                  <td colSpan={10} className="px-4 py-12 text-center text-slate-500">
                    Loading...
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={10} className="px-4 py-12 text-center text-slate-500">
                    No visible portfolio rows.
                  </td>
                </tr>
              ) : (
                rows.map((row) => (
                  <tr key={row.ticker} className="hover:bg-slate-50/80">
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-blue-700">
                      <Link to={`/stock?ticker=${encodeURIComponent(row.ticker)}`}>
                        {row.ticker}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatPercent(row.price_return_1y)}
                      <div className="text-xs text-slate-500">
                        from {formatMoney(row.prior_price_1y)}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatPercent(row.earnings_growth_1y)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatPercent(row.multiple_change_1y)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      <span
                        className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${scoreClass(row.compression_opportunity_score)}`}
                      >
                        {formatPercent(row.compression_opportunity_score)}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatMultiple(row.current_pe)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatMultiple(row.prior_pe_1y)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatPercent(row.revenue_growth_1y)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatCagrDecimal(row.base_cagr_y3)}
                    </td>
                    <td className="min-w-64 px-4 py-3 text-slate-600">
                      {explainRow(row)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {lastUpdated && !loading && (
          <p className="border-t border-slate-100 bg-slate-50 px-4 py-2 text-xs text-slate-500">
            Last updated {lastUpdated.toLocaleTimeString()} · Sorted by compression
            opportunity
          </p>
        )}
      </div>
    </div>
  )
}
