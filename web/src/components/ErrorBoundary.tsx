import { Component, type ReactNode } from 'react'

export default class ErrorBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(e: unknown) { console.error('[ProInspect] component failed:', e) }
  render() { return this.state.failed ? this.props.fallback : this.props.children }
}
