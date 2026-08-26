export type AuthMode = "login" | "register";

export function safeAuthNextPath(
  requested: string | null | undefined,
  fallback = "/account",
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

export function authSuccessPath(mode: AuthMode, requestedNext?: string | null): string {
  return mode === "register" ? "/verify-email" : safeAuthNextPath(requestedNext);
}
