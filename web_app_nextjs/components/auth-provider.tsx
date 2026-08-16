"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import * as api from "@/lib/api";
import { toast } from "sonner";

type AuthContextType = {
  user: api.User | null;
  token: string | null;
  refreshToken: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (payload: api.RegisterPayload) => Promise<void>;
  logout: () => void;
  refreshAccessToken: () => Promise<boolean>;
};

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<api.User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();

  const logout = useCallback(() => {
    setUser(null);
    setToken(null);
    setRefreshToken(null);
    router.push("/");
  }, [router]);

  const refreshAccessToken = useCallback(async () => {
    try {
      const result = await api.refreshToken();
      setToken(result.access_token);
      setRefreshToken(result.refresh_token);
      setUser({ username: result.username, role: result.role as "user" | "admin", email: result.email, phone: result.phone, display_name: result.display_name });
      return true;
    } catch (e) {
      logout();
      return false;
    }
  }, [logout]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    async function restoreSession() {
      try {
        const me = await api.getMe(token);
        setUser(me);
        // Cookie-only auth: the HttpOnly cookie carries the real token. We keep
        // a non-null placeholder in React state so components that guard on
        // `token` proceed and the API client sends `credentials: "include"`.
        setToken("cookie");
        setRefreshToken("cookie");
      } catch {
        const ok = await refreshAccessToken();
        if (!ok) logout();
      } finally {
        setIsLoading(false);
      }
    }

    restoreSession();
  }, [logout, refreshAccessToken]);

  // Periodic token refresh every 5 minutes
  useEffect(() => {
    if (!user) return;
    const interval = setInterval(() => {
      refreshAccessToken();
    }, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [user, refreshAccessToken]);

  const login = async (username: string, password: string) => {
    const result = await api.login(username, password);
    setToken(result.access_token);
    setRefreshToken(result.refresh_token);
    setUser({ username: result.username, role: result.role as "user" | "admin", email: result.email, phone: result.phone, display_name: result.display_name });
    router.push("/");
  };

  const register = async (payload: api.RegisterPayload) => {
    await api.register(payload);
    await login(payload.username, payload.password);
  };

  const value: AuthContextType = {
    user,
    token,
    refreshToken,
    isLoading,
    isAuthenticated: !!user,
    login,
    register,
    logout,
    refreshAccessToken,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
