import { useMemo, useState } from 'react'
import { importTransactions } from '../lib/api'

function formatNumber(value, maximumFractionDigits = 3) {
  return Number(value || 0).toLocaleString(undefined, { maximumFractionDigits })
}

function formatCurrency(value) {
  return Number(value || 0).toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
  })
}

export default function TransactionImport() {
  const [files, setFiles] = useState([])
  const [preview, setPreview] = useState(null)
  const [accountMappings, setAccountMappings] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const readyRows = useMemo(
    () => (preview?.rows || []).filter((row) => row.status === 'ready'),
    [preview],
  )

  async function readSelectedFiles(event) {
    const selected = [...event.target.files]
    setError('')
    setMessage('')
    setPreview(null)

    try {
      const payloads = await Promise.all(
        selected.map(async (file) => ({
          name: file.name,
          content: await file.text(),
        })),
      )
      setFiles(payloads)
    } catch {
      setError('One or more CSV files could not be read.')
      setFiles([])
    }
  }

  async function runPreview(mappings = accountMappings) {
    if (files.length === 0) {
      setError('Choose at least one transaction CSV.')
      return
    }

    setLoading(true)
    setError('')
    setMessage('')

    try {
      const payload = await importTransactions(files, mappings, false)
      setPreview(payload)

      const nextMappings = { ...mappings }
      for (const account of payload.accounts || []) {
        if (!nextMappings[account.key]) {
          nextMappings[account.key] = account.nickname || account.source_name
        }
      }
      setAccountMappings(nextMappings)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to preview transactions')
    } finally {
      setLoading(false)
    }
  }

  async function commitImport() {
    setLoading(true)
    setError('')
    setMessage('')

    try {
      const payload = await importTransactions(files, accountMappings, true)
      setPreview(payload)
      setMessage(
        `Imported ${payload.summary.imported.toLocaleString()} transactions. ` +
          `${payload.summary.duplicates.toLocaleString()} duplicates were skipped.`,
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to import transactions')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="mx-auto max-w-[96rem] px-4 py-8 sm:px-6 lg:px-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Import Transactions</h1>
        <p className="mt-1 text-sm text-slate-600">
          Preview Fidelity account-history CSVs, name the accounts, and import only new trades.
        </p>
      </div>

      {error && (
        <div className="mb-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </div>
      )}
      {message && (
        <div className="mb-5 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          {message}
        </div>
      )}

      <section className="border-y border-slate-200 bg-white py-5">
        <div className="flex flex-wrap items-end gap-4">
          <label className="min-w-72 flex-1 text-sm font-medium text-slate-700">
            Fidelity CSV files
            <input
              type="file"
              accept=".csv,text/csv"
              multiple
              onChange={readSelectedFiles}
              className="mt-2 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 file:mr-3 file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:font-medium file:text-slate-700"
            />
          </label>
          <button
            type="button"
            onClick={() => runPreview()}
            disabled={loading || files.length === 0}
            className="rounded-md bg-slate-900 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? 'Checking...' : 'Preview import'}
          </button>
        </div>
        {files.length > 0 && (
          <p className="mt-3 text-sm text-slate-500">
            {files.length} file{files.length === 1 ? '' : 's'} selected: {' '}
            {files.map((file) => file.name).join(', ')}
          </p>
        )}
      </section>

      {preview && (
        <>
          <section className="py-6">
            <h2 className="text-lg font-semibold text-slate-900">Account Names</h2>
            <p className="mt-1 text-sm text-slate-600">
              These nicknames are stored with imported transactions and will appear in account filters.
            </p>
            <div className="mt-4 grid gap-4 md:grid-cols-3">
              {(preview.accounts || []).map((account) => (
                <label key={account.key} className="text-sm font-medium text-slate-700">
                  {account.source_name}
                  {account.account_number_last4 && (
                    <span className="ml-2 font-normal text-slate-400">
                      ending {account.account_number_last4}
                    </span>
                  )}
                  <input
                    value={accountMappings[account.key] ?? account.nickname ?? ''}
                    onChange={(event) =>
                      setAccountMappings((current) => ({
                        ...current,
                        [account.key]: event.target.value,
                      }))
                    }
                    className="mt-2 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20"
                  />
                </label>
              ))}
            </div>
          </section>

          <section className="border-y border-slate-200 bg-white py-5">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                ['Trades found', preview.summary.parsed],
                ['Ready to import', preview.summary.ready],
                ['Duplicates', preview.summary.duplicates],
                ['Skipped rows', preview.summary.skipped],
              ].map(([label, value]) => (
                <div key={label}>
                  <div className="text-xs font-medium uppercase text-slate-500">{label}</div>
                  <div className="mt-1 text-2xl font-semibold text-slate-900">
                    {Number(value || 0).toLocaleString()}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-5 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => runPreview(accountMappings)}
                disabled={loading}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 disabled:opacity-50"
              >
                Refresh preview
              </button>
              <button
                type="button"
                onClick={commitImport}
                disabled={loading || readyRows.length === 0}
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                Import {readyRows.length.toLocaleString()} new transactions
              </button>
            </div>
          </section>

          <section className="py-6">
            <div className="mb-3 flex items-baseline justify-between gap-4">
              <h2 className="text-lg font-semibold text-slate-900">Transaction Preview</h2>
              <span className="text-sm text-slate-500">
                Showing {Math.min(preview.rows.length, 150)} of {preview.rows.length}
              </span>
            </div>
            <div className="overflow-x-auto border-y border-slate-200">
              <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
                <thead className="bg-slate-50 text-slate-600">
                  <tr>
                    {['Status', 'Date', 'Ticker', 'Type', 'Shares', 'Price', 'Fees', 'Account', 'File'].map(
                      (heading) => (
                        <th key={heading} className="whitespace-nowrap px-3 py-3 font-semibold">
                          {heading}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 bg-white">
                  {preview.rows.slice(0, 150).map((row, index) => (
                    <tr key={`${row.source_file}-${row.ticker}-${row.date}-${index}`}>
                      <td className="px-3 py-3">
                        <span
                          className={
                            row.status === 'duplicate'
                              ? 'text-amber-700'
                              : 'text-emerald-700'
                          }
                        >
                          {row.status === 'duplicate' ? 'Duplicate' : 'Ready'}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-3 text-slate-700">{row.date}</td>
                      <td className="px-3 py-3 font-medium text-slate-900">{row.ticker}</td>
                      <td className="px-3 py-3 capitalize text-slate-700">{row.type}</td>
                      <td className="px-3 py-3 text-slate-700">{formatNumber(row.shares)}</td>
                      <td className="px-3 py-3 text-slate-700">{formatCurrency(row.price_per_share)}</td>
                      <td className="px-3 py-3 text-slate-700">{formatCurrency(row.fees)}</td>
                      <td className="whitespace-nowrap px-3 py-3 text-slate-700">{row.account}</td>
                      <td className="whitespace-nowrap px-3 py-3 text-slate-500">{row.source_file}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
