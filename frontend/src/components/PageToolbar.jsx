export default function PageToolbar({ title, description, loading, onReload, onUpdatePrices }) {
  return (
    <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {description && (
          <p className="mt-1 max-w-xl text-sm text-slate-600">{description}</p>
        )}
      </div>
      <div className="flex flex-col items-stretch gap-2 sm:items-end">
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => onReload(false)}
            disabled={loading}
            title="Recalculate using cached prices and saved assumptions"
            className="inline-flex items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-60"
          >
            {loading ? 'Loading…' : 'Reload'}
          </button>
          <button
            type="button"
            onClick={() => onUpdatePrices(true)}
            disabled={loading}
            title="Fetch fresh Finnhub prices, then recalculate"
            className="inline-flex items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-800 disabled:opacity-60"
          >
            Update prices
          </button>
        </div>
        <p className="text-xs text-slate-500 sm:text-right">
          <span className="font-medium text-slate-600">Reload</span> — cached prices.{' '}
          <span className="font-medium text-slate-600">Update prices</span> — Finnhub fetch.
        </p>
      </div>
    </header>
  )
}
