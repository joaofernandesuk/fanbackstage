export type AuthMode = "login" | "register";

export const DEFAULT_LOGIN_DESTINATION = "/account";
export const DEFAULT_REGISTRATION_DESTINATION = "/welcome";
export const REGISTRATION_RETURN_STORAGE_KEY = "fanbackstage:registration-return";

const AUTH_TRANSFER_PATHS = new Set([
  "/forgot-password",
  "/login",
  "/register",
  "/reset-password",
  "/verify-email",
]);

export function safeAuthNextPath(
  requested: string | null | undefined,
  fallback = DEFAULT_LOGIN_DESTINATION,
): string {
  if (!requested || !requested.startsWith("/") || requested.startsWith("//")) return fallback;
  try {
    const base = new URL("https://fanbackstage.local");
    const resolved = new URL(requested, base);
    if (resolved.origin !== base.origin) return fallback;
    return `${resolved.pathname}${resolved.search}${resolved.hash}`;
  } catch {
    return fallback;
  }
}

export function safeAuthReturnPath(
  requested: string | null | undefined,
  fallback = DEFAULT_LOGIN_DESTINATION,
): string {
  const resolved = safeAuthNextPath(requested, fallback);
  const pathname = new URL(resolved, "https://fanbackstage.local").pathname;
  return AUTH_TRANSFER_PATHS.has(pathname) ? fallback : resolved;
}

export function authEntryPath(mode: AuthMode, requestedNext?: string | null): string {
  const fallback = mode === "register"
    ? DEFAULT_REGISTRATION_DESTINATION
    : DEFAULT_LOGIN_DESTINATION;
  const next = safeAuthReturnPath(requestedNext, fallback);
  return `/${mode === "register" ? "register" : "login"}?next=${encodeURIComponent(next)}`;
}

export function authSuccessPath(mode: AuthMode, requestedNext?: string | null): string {
  const fallback = mode === "register"
    ? DEFAULT_REGISTRATION_DESTINATION
    : DEFAULT_LOGIN_DESTINATION;
  const next = safeAuthReturnPath(requestedNext, fallback);
  return mode === "register"
    ? `/verify-email?next=${encodeURIComponent(next)}`
    : next;
}

type AuthReturnStorage = Pick<Storage, "getItem" | "removeItem" | "setItem">;

export function rememberRegistrationReturn(
  storage: AuthReturnStorage,
  requestedNext?: string | null,
): string {
  const next = safeAuthReturnPath(requestedNext, DEFAULT_REGISTRATION_DESTINATION);
  try {
    storage.setItem(REGISTRATION_RETURN_STORAGE_KEY, next);
  } catch {
    // The query-string handoff remains authoritative when browser storage is unavailable.
  }
  return next;
}

export function registrationReturn(
  storage: AuthReturnStorage,
  requestedNext?: string | null,
): string {
  let stored: string | null = null;
  try {
    stored = storage.getItem(REGISTRATION_RETURN_STORAGE_KEY);
  } catch {
    // Cross-tab persistence is best-effort; the safe welcome fallback still works.
  }
  return safeAuthReturnPath(
    requestedNext ?? stored,
    DEFAULT_REGISTRATION_DESTINATION,
  );
}

export function clearRegistrationReturn(storage: AuthReturnStorage): void {
  try {
    storage.removeItem(REGISTRATION_RETURN_STORAGE_KEY);
  } catch {
    // Storage denial must not turn successful email verification into a UI failure.
  }
}

export function authErrorMessage(
  mode: AuthMode,
  status: number | undefined,
  detail: unknown,
): string {
  if (status === 401) return "The email address or password is incorrect.";
  if (status === 403 && detail === "Verify your email address before logging in.") {
    return detail;
  }
  if (status === 403) return "This account is not available. Contact support if you think this is a mistake.";
  if (status === 409) return "An account with this email address already exists.";
  if (status === 422) {
    return mode === "register"
      ? "Check your email, use at least 12 password characters, and confirm you are at least 18."
      : "Check the email address and password, then try again.";
  }
  if (status === 429) return "Too many attempts. Wait a moment, then try again.";
  if (status && status >= 500) return "FanBackstage could not complete this request. Try again shortly.";
  return "Please check the form fields and try again.";
}
