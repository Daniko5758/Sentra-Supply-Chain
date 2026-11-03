import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { Button } from './Button';

/** Redirects unauthenticated visitors to Login and preserves their requested URL. */
export function RequireAuth() {
  const { status, error, retry } = useAuth();
  const location = useLocation();

  if (status === 'checking') {
    return <div className="auth-state" role="status">Checking your session…</div>;
  }

  if (status === 'error') {
    return (
      <div className="auth-state" role="alert">
        <p>{error?.message ?? 'Unable to check your session. Please try again.'}</p>
        <Button variant="secondary" onClick={retry}>Try again</Button>
      </div>
    );
  }

  if (status !== 'authenticated') {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}
