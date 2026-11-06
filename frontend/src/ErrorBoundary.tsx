import { Component, type ReactNode } from "react";

/** Catches a crash inside one view so the nav stays usable instead of the whole dashboard going blank. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error) { console.error("View crashed:", error); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <section className="card" role="alert">
        <h2>Something went wrong in this view</h2>
        <p className="muted">{this.state.error.message}</p>
        <button onClick={() => this.setState({ error: null })}>Try again</button>
      </section>
    );
  }
}
