import { Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { ChatPanel } from './ChatPanel';
import { ErrorBoundary } from './ErrorBoundary';
import { Header } from './Header';
import { Sidebar } from './Sidebar';

/** Latar aurora bergerak pelan di belakang shell (animation.md §4.14). Tidak pernah di belakang angka langsung. */
export function Backdrop() {
  return (
    <div className="app-backdrop" aria-hidden="true">
      <span className="blob blob--1" />
      <span className="blob blob--2" />
      <span className="blob blob--3" />
    </div>
  );
}

export function Layout() {
  const { me } = useAuth();
  const { pathname } = useLocation();

  return (
    <div className="app-shell">
      <Backdrop />
      <Header />
      <Sidebar featureFlags={me?.feature_flags} role={me?.role} />
      <main className="app-main">
        {/* key per rute → transisi masuk tiap ganti halaman (animation.md §4.12) */}
        <div key={pathname} className="page-transition">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </div>
      </main>
      <ChatPanel />
    </div>
  );
}
