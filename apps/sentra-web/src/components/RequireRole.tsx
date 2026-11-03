import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import type { Role } from '../api/types';

interface RequireRoleProps {
  roles: Role[];
  children: ReactNode;
}

/** Keeps a role-restricted UI out of routes for users without that role. */
export function RequireRole({ roles, children }: RequireRoleProps) {
  const { me } = useAuth();
  if (!me || !roles.includes(me.role)) {
    return <Navigate to="/dashboard" replace />;
  }
  return <>{children}</>;
}
