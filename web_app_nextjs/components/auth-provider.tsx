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
    if (typeof window !== "undefined") {
      localStorage.removeItem("kb_token");
      localStorage.removeItem("kb_refresh_token");
      localStorage.removeItem("kb_user");
    }
    router.push("/");
  }, [router]);

  const refreshAccessToken = useCallback(async () => {
    let rt = typeof window !== "undefined" ? localStorage.getItem("kb_refresh_token") : null;
    if (!rt || rt === "null" || rt === "undefined") {
      logout();
      return false;
    }
    try {
      const result = await api.refreshToken(rt);
      setToken(result.access_token);
      setRefreshToken(result.refresh_token);
      setUser({ username: result.username, role: result.role as "user" | "admin", email: result.email, phone: result.phone, display_name: result.display_name });
      localStorage.setItem("kb_token", result.access_token);
      localStorage.setItem("kb_refresh_token", result.refresh_token);
      localStorage.setItem("kb_user", JSON.stringify({ username: result.username, role: result.role, email: result.email, phone: result.phone, display_name: result.display_name }));
      return true;
    } catch (e) {
      logout();
      return false;
    }
  }, [logout]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    let storedToken = localStorage.getItem("kb_token");
    const storedRefresh = localStorage.getItem("kb_refresh_token");
    const storedUser = localStorage.getItem("kb_user");
    if (!storedToken || storedToken === "null" || storedToken === "undefined") storedToken = null;

    async function restoreSession() {
      if (!storedToken) {
        if (storedRefresh) {
          const ok = await refreshAccessToken();
          if (ok) {
            setIsLoading(false);
            return;
          }
        }
        setIsLoading(false);
        return;
      }
      try {
        await api.getMe(storedToken);
        const parsedUser = storedUser ? JSON.parse(storedUser) : null;
        setToken(storedToken);
        setRefreshToken(storedRefresh);
        setUser(parsedUser);
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
    if (!token) return;
    const interval = setInterval(() => {
      refreshAccessToken();
    }, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [token, refreshAccessToken]);

  const login = async (username: string, password: string) => {
    const result = await api.login(username, password);
    setToken(result.access_token);
    setRefreshToken(result.refresh_token);
    setUser({ username: result.username, role: result.role as "user" | "admin", email: result.email, phone: result.phone, display_name: result.display_name });
    localStorage.setItem("kb_token", result.access_token);
    localStorage.setItem("kb_refresh_token", result.refresh_token);
    localStorage.setItem("kb_user", JSON.stringify({ username: result.username, role: result.role, email: result.email, phone: result.phone, display_name: result.display_name }));
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
    isAuthenticated: !!user && !!token,
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
