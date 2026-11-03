import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api, ApiError, setUnauthorizedHandler } from '../api/client';
import { useMe } from '../api/hooks';
import type { LoginRequest, LoginResponse, Me } from '../api/types';

export type AuthStatus = 'checking' | 'authenticated' | 'unauthenticated' | 'error';

interface AuthContextValue {
  me: Me | undefined;
  status: AuthStatus;
  error: Error | null;
  isScmManager: boolean;
  signIn(credentials: LoginRequest): Promise<void>;
  signOut(): Promise<void>;
  isSigningOut: boolean;
  /** Checks the session again after `status === 'error'` (EP-03 failed for another reason than 401). */
  retry(): void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function isUnauthenticated(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 401;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const meQuery = useMe();
  const refetchMe = meQuery.refetch;
  const [forceSignedOut, setForceSignedOut] = useState(false);
  const [isSigningOut, setIsSigningOut] = useState(false);

  useEffect(() => {
    if (meQuery.data) {
      setForceSignedOut(false);
    }
  }, [meQuery.data]);

  useEffect(
    () =>
      setUnauthorizedHandler(() => {
        setForceSignedOut(true);
        queryClient.removeQueries({ queryKey: ['me'] });
      }),
    [queryClient],
  );

  const signIn = useCallback(
    async (credentials: LoginRequest) => {
      await api.post<LoginResponse>('/auth/login', credentials);
      const profile = await api.get<Me>('/me');
      queryClient.setQueryData(['me'], profile);
      setForceSignedOut(false);
    },
    [queryClient],
  );

  const signOut = useCallback(async () => {
    setIsSigningOut(true);
    try {
      await api.post<void>('/auth/logout');
      setForceSignedOut(true);
      // Every cached response belongs to the account that signed out (cases, approvals, ERP results kept for the session),
      // so nothing is left for the next account that signs in on this tab.
      queryClient.clear();
    } finally {
      setIsSigningOut(false);
    }
  }, [queryClient]);

  const value = useMemo<AuthContextValue>(() => {
    const me = forceSignedOut ? undefined : meQuery.data;
    const error = meQuery.error instanceof Error ? meQuery.error : null;
    let status: AuthStatus;

    if (me) {
      status = 'authenticated';
    } else if (forceSignedOut || isUnauthenticated(meQuery.error)) {
      status = 'unauthenticated';
    } else if (meQuery.isPending) {
      status = 'checking';
    } else {
      status = 'error';
    }

    return {
      me,
      status,
      error,
      isScmManager: me?.role === 'SCM_MANAGER',
      signIn,
      signOut,
      isSigningOut,
      retry: () => void refetchMe(),
    };
  }, [forceSignedOut, isSigningOut, meQuery.data, meQuery.error, meQuery.isPending, refetchMe, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used inside AuthProvider.');
  }
  return context;
}
