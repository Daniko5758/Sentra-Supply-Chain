import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button } from './Button';

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Menu dan header tetap tampil bila boundary hanya membungkus isi halaman. */
  scope?: 'page' | 'app';
}

interface ErrorBoundaryState {
  failed: boolean;
}

/** Menangkap galat saat render supaya satu nilai tak terduga tidak mengosongkan seluruh layar. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('SENTRA UI error', error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;

    return (
      <section className="card" role="alert">
        <h1>Something went wrong</h1>
        <p className="page-description">This page could not be displayed. Your data is not affected.</p>
        <Button variant="secondary" onClick={() => this.setState({ failed: false })}>Try again</Button>
        {this.props.scope === 'app' && (
          <Button variant="ghost" onClick={() => window.location.reload()}>Reload page</Button>
        )}
      </section>
    );
  }
}
