"use client";

import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

import {
  AuthMode,
  DEFAULT_LOGIN_DESTINATION,
  DEFAULT_REGISTRATION_DESTINATION,
  safeAuthReturnPath,
} from "../lib/auth-ui";
import { AuthDialog } from "./auth-dialog";

type AuthRequest = {
  mode: AuthMode;
  nextPath: string;
};

type AuthExperienceValue = {
  openAuth: (mode?: AuthMode, nextPath?: string | null) => void;
};

const AuthExperienceContext = createContext<AuthExperienceValue | null>(null);

function browserPath(fallback: string) {
  if (typeof window === "undefined") return fallback;
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

export function AuthExperienceProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<AuthRequest | null>(null);

  const openAuth = useCallback((mode: AuthMode = "login", requestedNext?: string | null) => {
    const fallback = mode === "register"
      ? DEFAULT_REGISTRATION_DESTINATION
      : DEFAULT_LOGIN_DESTINATION;
    const current = browserPath(fallback);
    setRequest({
      mode,
      nextPath: safeAuthReturnPath(requestedNext ?? current, fallback),
    });
  }, []);

  const value = useMemo(() => ({ openAuth }), [openAuth]);

  return (
    <AuthExperienceContext.Provider value={value}>
      {children}
      {request && (
        <AuthDialog
          mode={request.mode}
          nextPath={request.nextPath}
          onClose={() => setRequest(null)}
          onModeChange={(mode) => setRequest((current) => current ? { ...current, mode } : null)}
        />
      )}
    </AuthExperienceContext.Provider>
  );
}

export function useAuthExperience() {
  const value = useContext(AuthExperienceContext);
  if (!value) throw new Error("useAuthExperience must be used within AuthExperienceProvider");
  return value;
}
