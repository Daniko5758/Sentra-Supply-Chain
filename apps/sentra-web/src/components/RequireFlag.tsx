import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import type { FeatureFlags } from '../api/types';

interface RequireFlagProps {
  flag: keyof FeatureFlags;
  children: ReactNode;
}

/** Halaman fitur opsional hanya bisa dibuka bila flag-nya menyala; selain itu kembali ke Dashboard. */
export function RequireFlag({ flag, children }: RequireFlagProps) {
  const { me, status } = useAuth();
  if (status === 'checking') {
    return null;
  }
  if (me?.feature_flags[flag] !== true) {
    return <Navigate to="/dashboard" replace />;
  }
  return <>{children}</>;
}
