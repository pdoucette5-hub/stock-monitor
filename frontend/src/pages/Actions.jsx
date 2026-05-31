import MetricCard from '../components/MetricCard'
import PageToolbar from '../components/PageToolbar'
import { usePortfolioView } from '../hooks/usePortfolioView'
import { actionBadgeClass, queueActionBadgeClass } from '../lib/actions'
import { formatMoney, formatNum, formatWeightDecimal } from '../lib/format'

export default function Actions() {
  // We only need the core portfolio view logic now!
  const { view, loading, error, lastUpdated, load } = usePortfolioView()

  const actionQueue = view?.action_queue ?? []
  const queueSummary = view?.action_queue_summary ?? {}
  const rebalanceStepPct = Number(queueSummary.rebalance_step_pct ?? 25)

  return (
    <div className="mx-auto max-w-[96rem] px-4 py-8 sm:px-6 lg:px-8">
      <PageToolbar
        title="Action Queue"
        description="Trade recommendations for included holdings based on your saved redistribution settings."
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

      <section>
        <div className="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <MetricCard
            label="Redistribution pool"
            value={formatMoney(queueSummary.redistribution_pool_value)}
          />
          <MetricCard label="Total buy" value={formatMoney(queueSummary.total_buy_dollars)} />
          <MetricCard label="Total trim" value={formatMoney(queueSummary.total_trim_dollars)} />
          <MetricCard label="Trade actions" value={formatNum(queueSummary.action_count)} />
        </div>

        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-3 font-semibold text-slate-700">Ticker</th>
                  <th className="px-4 py-3 font-semibold text-slate-700">
                    Rating
                  </th>
                  <th className="px-4 py-3 font-semibold text-slate-700">
                    Trade
                  </th>
                  <th className="px-4 py-3 font-semibold text-slate-700">Reason</th>
                  <th className="px-4 py-3 font-semibold text-slate-700">
                    Dollar trade
                  </th>
                  <th className="px-4 py-3 font-semibold text-slate-700">Est. shares</th>
                  <th className="px-4 py-3 font-semibold text-slate-700">
                    Target weight
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {loading && actionQueue.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-slate-500">
                      Loading…
                    </td>
                  </tr>
                ) : actionQueue.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-slate-500">
                      No action queue rows. Check your Redistribution settings and reload.
                    </td>
                  </tr>
                ) : (
                  actionQueue.map((row) => (
                    <tr key={row.ticker} className="hover:bg-slate-50/80">
                      <td className="px-4 py-3 font-medium">{row.ticker}</td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${actionBadgeClass(row.action)}`}
                        >
                          {row.action ?? '—'}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex rounded-full px-2.5 py-1 text-xs ${queueActionBadgeClass(row.recommended_action)}`}
                        >
                          {row.recommended_action}
                        </span>
                      </td>
                      <td className="max-w-md px-4 py-3 text-slate-600">
                        {row.reason ?? '—'}
                      </td>
                      <td className="px-4 py-3 tabular-nums">
                        {formatMoney(row.dollar_trade)}
                      </td>
                      <td className="px-4 py-3 tabular-nums">
                        {formatNum(row.shares_trade)}
                      </td>
                      <td className="px-4 py-3 tabular-nums">
                        {formatWeightDecimal(row.target_weight_effective)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          {lastUpdated && !loading && (
            <p className="border-t border-slate-100 bg-slate-50 px-4 py-2 text-xs text-slate-500">
              Queue reflects saved participation and moves {rebalanceStepPct.toFixed(0)}%
              toward target weights · {lastUpdated.toLocaleTimeString()}
            </p>
          )}
        </div>
      </section>
    </div>
  )
}
