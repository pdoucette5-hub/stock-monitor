import { Navigate, NavLink, Route, Routes } from 'react-router-dom'
import Actions from './pages/Actions'
import Holdings from './pages/Holdings'
import StockDetail from './pages/StockDetail'
import Redistribution from './pages/Redistribution' // <-- Added Import

const navLinkClass = ({ isActive }) =>
  [
    'rounded-md px-3 py-2 text-sm font-medium transition',
    isActive
      ? 'bg-slate-100 text-slate-900'
      : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900',
  ].join(' ')

export default function App() {
  return (
    // Added flex flex-col to keep the footer pushed to the bottom
    <div className="min-h-screen flex flex-col bg-slate-50 text-slate-900">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/95 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-white/80">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-6 px-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div
              className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-900 text-sm font-bold text-white"
              aria-hidden
            >
              SM
            </div>
            <span className="text-base font-semibold tracking-tight text-slate-900">
              Stock Monitor
            </span>
          </div>

          <nav
            className="flex items-center gap-1"
            aria-label="Main navigation"
          >
            <NavLink to="/holdings" className={navLinkClass}>
              Holdings
            </NavLink>
            <NavLink to="/actions" className={navLinkClass}>
              Actions
            </NavLink>
            {/* Added Redistribution Link */}
            <NavLink to="/redistribution" className={navLinkClass}>
              Redistribution
            </NavLink>
            <NavLink to="/stock" className={navLinkClass}>
              Stock Detail
            </NavLink>
          </nav>
        </div>
      </header>

      {/* Added flex-grow to push the footer down */}
      <main className="flex-grow">
        <Routes>
          <Route path="/" element={<Navigate to="/holdings" replace />} />
          <Route path="/holdings" element={<Holdings />} />
          <Route path="/actions" element={<Actions />} />
          {/* Added Redistribution Route */}
          <Route path="/redistribution" element={<Redistribution />} />
          <Route path="/stock" element={<StockDetail />} />
        </Routes>
      </main>

      <footer className="mt-auto border-t border-slate-200 bg-white py-6">
        <p className="text-center text-xs text-slate-500">
          Data from FastAPI · cache/scenario_inputs.json
        </p>
      </footer>
    </div>
  )
}