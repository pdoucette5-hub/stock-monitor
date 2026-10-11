import { useEffect, useState } from 'react'
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
import Compression from './pages/Compression'
import Forecasts from './pages/Forecasts'
import Compensation from './pages/Compensation'
import { useAuth } from './auth/AuthContext'

function navClass({ isActive }) {
  return [
    'rounded-md px-2 py-2 text-sm font-medium transition',
    isActive
      ? 'bg-slate-900 text-white dark:bg-blue-600'
      : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white',
  ].join(' ')
}

function initialDarkMode() {
  const saved = localStorage.getItem('stock-monitor-theme')
  if (saved) return saved === 'dark'
  return true
}

export default function App() {
  const { authEnabled, user, signOut } = useAuth()
  const [darkMode, setDarkMode] = useState(initialDarkMode)
  const hasFullAccess = !authEnabled || user?.role !== 'limited'
  const defaultPath = '/'

  useEffect(() => {
    document.documentElement.classList.toggle('dark', darkMode)
    localStorage.setItem('stock-monitor-theme', darkMode ? 'dark' : 'light')
  }, [darkMode])

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 transition-colors dark:bg-slate-950 dark:text-slate-100">
      <header className="border-b border-slate-200 bg-white transition-colors dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex w-[98vw] items-center justify-between px-4 py-4">
          <div>
            <h1 className="text-lg font-semibold text-slate-900 dark:text-white">Stock Monitor</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400">
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
            <NavLink to="/compression" className={navClass}>
              Compression
            </NavLink>
            {hasFullAccess && (
              <>
                <NavLink to="/redistribution" className={navClass}>
                  Redistribution
                </NavLink>
                <NavLink to="/actions" className={navClass}>
                  Actions
                </NavLink>
                <NavLink to="/performance" className={navClass}>
                  Performance
                </NavLink>
                <NavLink to="/forecasts" className={navClass}>
                  Forecasts
                </NavLink>
                <NavLink to="/matthew" className={navClass}>
                  Matthew
                </NavLink>
                <NavLink to="/management" className={navClass}>
                  Management
                </NavLink>
              </>
            )}
            <NavLink to="/change-log" className={navClass}>
              Change Log
            </NavLink>
            {hasFullAccess && (
              <>
                <NavLink to="/import" className={navClass}>
                  Import
                </NavLink>
                <NavLink to="/archived" className={navClass}>
                  Archived
                </NavLink>
              </>
            )}
            <NavLink to="/stock" className={navClass}>
              Stock Detail
            </NavLink>
            <button
              type="button"
              onClick={() => setDarkMode((current) => !current)}
              className="rounded-md border border-slate-200 px-2.5 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white"
              aria-label={`Switch to ${darkMode ? 'light' : 'dark'} mode`}
              title={`Switch to ${darkMode ? 'light' : 'dark'} mode`}
            >
              <span aria-hidden="true">{darkMode ? '☀︎' : '◐'}</span>
              <span className="ml-1.5">{darkMode ? 'Light' : 'Dark'}</span>
            </button>
            {authEnabled && (
              <button
                type="button"
                onClick={signOut}
                className="rounded-md border border-slate-200 px-2 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white"
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
          <Route path="/compression" element={<Compression />} />
          <Route
            path="/redistribution"
            element={hasFullAccess ? <Redistribution /> : <Navigate to={defaultPath} replace />}
          />
          <Route
            path="/actions"
            element={hasFullAccess ? <Actions /> : <Navigate to={defaultPath} replace />}
          />
          <Route
            path="/performance"
            element={hasFullAccess ? <Performance /> : <Navigate to={defaultPath} replace />}
          />
          <Route
            path="/forecasts"
            element={hasFullAccess ? <Forecasts /> : <Navigate to={defaultPath} replace />}
          />
          <Route
            path="/matthew"
            element={hasFullAccess ? <Compensation /> : <Navigate to={defaultPath} replace />}
          />
          <Route
            path="/compensation"
            element={<Navigate to="/matthew" replace />}
          />
          <Route
            path="/management"
            element={hasFullAccess ? <Management /> : <Navigate to={defaultPath} replace />}
          />
          <Route path="/change-log" element={<ChangeLog />} />
          <Route
            path="/import"
            element={hasFullAccess ? <TransactionImport /> : <Navigate to={defaultPath} replace />}
          />
          <Route
            path="/archived"
            element={hasFullAccess ? <Archived /> : <Navigate to={defaultPath} replace />}
          />
          <Route path="/stock" element={<StockDetail />} />
          <Route path="*" element={<Navigate to={defaultPath} replace />} />
        </Routes>
      </main>
    </div>
  )
}
