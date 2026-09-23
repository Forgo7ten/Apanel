"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import {
  authErrorMessage,
  login as loginRequest,
  logout as logoutRequest,
  refreshSession,
  register as registerRequest,
  setSession,
  type AuthSession,
  type AuthUser,
} from "@/lib/auth-session";
import { clearSession, getSession, subscribeToSession } from "@/lib/auth-session";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

type AuthContextValue = {
  status: AuthStatus;
  user: AuthUser | null;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<AuthSession>;
  register: (username: string, password: string, inviteToken: string | null) => Promise<AuthSession>;
  logout: () => Promise<void>;
  refresh: () => Promise<AuthSession | null>;
  getErrorMessage: (error: unknown) => string;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const initialSession = getSession();
  const [status, setStatus] = useState<AuthStatus>(initialSession ? "authenticated" : "loading");
  const [user, setUser] = useState<AuthUser | null>(initialSession?.user ?? null);
  const operationId = useRef(0);

  const commitSession = useCallback((session: AuthSession | null) => {
    setUser(session?.user ?? null);
    setStatus(session ? "authenticated" : "unauthenticated");
  }, []);

  useEffect(() => subscribeToSession(commitSession), [commitSession]);

  useEffect(() => {
    const operation = operationId.current;

    void refreshSession(undefined, { commit: false }).then((session) => {
      if (operation !== operationId.current) {
        return;
      }

      if (session) {
        setSession(session);
      } else if (getSession()) {
        // A discarded refresh may follow a local or cross-tab logout. Do not
        // emit another clear signal when there is already no local session.
        clearSession();
      }
      commitSession(session);
    });
  }, [commitSession]);

  const login = useCallback(async (username: string, password: string) => {
    operationId.current += 1;

    try {
      return await loginRequest(username, password);
    } catch (error) {
      if (!getSession()) {
        commitSession(null);
      }
      throw error;
    }
  }, [commitSession]);

  const register = useCallback(async (username: string, password: string, inviteToken: string | null) => {
    operationId.current += 1;

    try {
      return await registerRequest(username, password, inviteToken);
    } catch (error) {
      if (!getSession()) {
        commitSession(null);
      }
      throw error;
    }
  }, [commitSession]);

  const logout = useCallback(async () => {
    operationId.current += 1;
    await logoutRequest();
    commitSession(null);
  }, [commitSession]);

  const refresh = useCallback(async () => {
    const session = await refreshSession();
    commitSession(session);
    return session;
  }, [commitSession]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      isAuthenticated: status === "authenticated",
      login,
      register,
      logout,
      refresh,
      getErrorMessage: authErrorMessage,
    }),
    [login, logout, refresh, register, status, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }

  return context;
}
