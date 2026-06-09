import { Component } from 'react'

export default class AppErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Stock Monitor render error', error, info)
  }

  render() {
    if (!this.state.error) {
      return this.props.children
    }

    return (
      <div className="mx-auto max-w-3xl px-6 py-16">
        <div className="rounded-lg border border-red-200 bg-white p-6 shadow-sm">
          <h1 className="text-xl font-semibold text-slate-900">
            Stock Monitor could not render this page
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            Refresh the page. If the problem continues, the message below identifies
            the component error.
          </p>
          <pre className="mt-4 overflow-auto rounded-md bg-red-50 p-3 text-xs text-red-800">
            {String(this.state.error?.message || this.state.error)}
          </pre>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-4 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
          >
            Reload page
          </button>
        </div>
      </div>
    )
  }
}
