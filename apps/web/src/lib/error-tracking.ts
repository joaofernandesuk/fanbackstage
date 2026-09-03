export type SafeErrorContext = {
  category: "browser_unhandled_exception" | "browser_unhandled_rejection" | "web_runtime_error";
  environment: string;
  releaseSha: string;
  route: string;
};

export type BrowserErrorTransport = (error: unknown, context: SafeErrorContext) => void;

type BrowserTarget = {
  addEventListener(type: "error" | "unhandledrejection", listener: EventListener): void;
  removeEventListener(type: "error" | "unhandledrejection", listener: EventListener): void;
  location: { href: string };
};

export type BrowserErrorTrackingConfig = {
  dsn: string;
  enabled: boolean;
  environment: string;
  releaseSha: string;
};

const WINDOW_SECONDS = 60;
const MAX_EVENTS_PER_WINDOW = 5;

export function browserErrorTrackingConfig(
  environment: Record<string, string | undefined> = process.env,
): BrowserErrorTrackingConfig {
  const provider = environment.NEXT_PUBLIC_FANBACKSTAGE_ERROR_TRACKING_PROVIDER;
  const dsn = environment.NEXT_PUBLIC_FANBACKSTAGE_ERROR_TRACKING_DSN?.trim() ?? "";
  const runtimeEnvironment = environment.NEXT_PUBLIC_FANBACKSTAGE_ENVIRONMENT ?? "development";
  const releaseSha = environment.NEXT_PUBLIC_FANBACKSTAGE_RELEASE_SHA?.trim() ?? "development";
  return {
    dsn,
    enabled: provider === "sentry" && Boolean(dsn) && runtimeEnvironment !== "test",
    environment: runtimeEnvironment,
    releaseSha,
  };
}

export function safeRoute(value: string): string {
  try {
    return new URL(value, "https://fanbackstage.invalid").pathname || "/";
  } catch {
    return "unmatched_route";
  }
}

export function scrubErrorEvent<T extends object>(event: T): T {
  const safe = { ...event } as Record<string, unknown>;
  delete safe.request;
  delete safe.user;
  delete safe.breadcrumbs;
  delete safe.extra;
  delete safe.fingerprint;
  delete safe.logentry;
  delete safe.server_name;
  delete safe.spans;
  delete safe.transaction;
  if (safe["message"] && safe["message"] !== "fanbackstage_error_tracking_diagnostic") {
    safe["message"] = "[redacted]";
  }
  for (const containerName of ["exception", "threads"] as const) {
    const container = safe[containerName] as
      | { values?: Array<Record<string, unknown>> }
      | undefined;
    for (const value of container?.values ?? []) {
      if (containerName === "exception") value.value = "[redacted]";
      const stacktrace = value.stacktrace as
        | { frames?: Array<Record<string, unknown>> }
        | undefined;
      for (const frame of stacktrace?.frames ?? []) delete frame.vars;
    }
  }
  if (safe.tags && typeof safe.tags === "object") {
    safe.tags = Object.fromEntries(
      Object.entries(safe.tags).filter(([key]) => key.startsWith("fanbackstage.")),
    );
  }
  if (safe["contexts"] && typeof safe["contexts"] === "object") {
    const contexts = safe["contexts"] as Record<string, unknown>;
    safe["contexts"] = Object.fromEntries(
      ["browser", "fanbackstage", "os", "runtime"]
        .filter((key) => contexts[key] !== undefined)
        .map((key) => [key, contexts[key]]),
    );
  }
  return safe as T;
}

export function installBrowserErrorHandlers(
  target: BrowserTarget,
  transport: BrowserErrorTransport,
  config: BrowserErrorTrackingConfig,
  now: () => number = Date.now,
): () => void {
  if (!config.enabled) return () => undefined;
  let windowStartedAt = now();
  let eventsInWindow = 0;
  const report = (error: unknown, category: SafeErrorContext["category"]) => {
    const current = now();
    if (current - windowStartedAt >= WINDOW_SECONDS * 1_000) {
      windowStartedAt = current;
      eventsInWindow = 0;
    }
    if (eventsInWindow >= MAX_EVENTS_PER_WINDOW) return;
    eventsInWindow += 1;
    transport(error, {
      category,
      environment: config.environment,
      releaseSha: config.releaseSha,
      route: safeRoute(target.location.href),
    });
  };
  const onError: EventListener = (event) => {
    const errorEvent = event as ErrorEvent;
    report(errorEvent.error ?? new Error("Unhandled browser error"), "browser_unhandled_exception");
  };
  const onRejection: EventListener = (event) => {
    const rejection = event as PromiseRejectionEvent;
    report(rejection.reason ?? new Error("Unhandled promise rejection"), "browser_unhandled_rejection");
  };
  target.addEventListener("error", onError);
  target.addEventListener("unhandledrejection", onRejection);
  return () => {
    target.removeEventListener("error", onError);
    target.removeEventListener("unhandledrejection", onRejection);
  };
}
