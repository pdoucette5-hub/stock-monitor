import { Navigate, NavLink, Route, Routes } from 'react-router-dom'
import Holdings from './pages/Holdings'
import Watchlist from './pages/Watchlist'
import Redistribution from './pages/Redistribution'
import Actions from './pages/Actions'
import StockDetail from './pages/StockDetail'
import Archived from './pages/Archived'
import Performance from './pages/Performance'
import ChangeLog from './pages/ChangeLog'
import TransactionImport from './pages/TransactionImport'
import Management from './pages/Management'
import { useAuth } from './auth/AuthContext'

function navClass({ isActive }) {
  return [
    'rounded-md px-2 py-2 text-sm font-medium transition',
    isActive
      ? 'bg-slate-900 text-white'
      : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
  ].join(' ')
}

export default function App() {
  const { authEnabled, user, signOut } = useAuth()

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex w-[98vw] items-center justify-between px-4 py-4">
          <div>
            <h1 className="text-lg font-semibold text-slate-900">Stock Monitor</h1>
            <p className="text-xs text-slate-500">
              Portfolio, watchlist, and valuation workflow
            </p>
          </div>
          <nav className="flex flex-wrap items-center justify-end gap-1">
            <NavLink to="/" end className={navClass}>
              Holdings
            </NavLink>
            <NavLink to="/watchlist" className={navClass}>
              Watchlist
            </NavLink>
            <NavLink to="/redistribution" className={navClass}>
              Redistribution
            </NavLink>
            <NavLink to="/actions" className={navClass}>
              Actions
            </NavLink>
            <NavLink to="/performance" className={navClass}>
              Performance
            </NavLink>
            <NavLink to="/management" className={navClass}>
              Management
            </NavLink>
            <NavLink to="/change-log" className={navClass}>
              Change Log
            </NavLink>
            <NavLink to="/import" className={navClass}>
              Import
            </NavLink>
            <NavLink to="/archived" className={navClass}>
              Archived
            </NavLink>
            <NavLink to="/stock" className={navClass}>
              Stock Detail
            </NavLink>
            {authEnabled && (
              <button
                type="button"
                onClick={signOut}
                className="rounded-md border border-slate-200 px-2 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                title={user?.email || 'Signed in'}
              >
                Sign out
              </button>
            )}
          </nav>
        </div>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Holdings />} />
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/redistribution" element={<Redistribution />} />
          <Route path="/actions" element={<Actions />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="/management" element={<Management />} />
          <Route path="/change-log" element={<ChangeLog />} />
          <Route path="/import" element={<TransactionImport />} />
          <Route path="/archived" element={<Archived />} />
          <Route path="/stock" element={<StockDetail />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}
