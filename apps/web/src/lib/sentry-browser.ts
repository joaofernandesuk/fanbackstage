import * as Sentry from "@sentry/nextjs";

import {
  browserErrorTrackingConfig,
  installBrowserErrorHandlers,
  safeRoute,
  scrubErrorEvent,
  type SafeErrorContext,
} from "./error-tracking";

const config = browserErrorTrackingConfig();
let initialized = false;

function capture(error: unknown, context: SafeErrorContext) {
  Sentry.withScope((scope) => {
    scope.setTag("fanbackstage.category", context.category);
    scope.setContext("fanbackstage", {
      environment: context.environment,
      release_sha: context.releaseSha,
      route: context.route,
    });
    Sentry.captureException(error);
  });
}

export function initializeBrowserErrorTracking() {
  if (!config.enabled || initialized || typeof window === "undefined") return;
  initialized = true;
  Sentry.init({
    beforeSend: (event) => scrubErrorEvent(event),
    defaultIntegrations: false,
    dsn: config.dsn,
    environment: config.environment,
    maxBreadcrumbs: 0,
    release: config.releaseSha,
    replaysOnErrorSampleRate: 0,
    replaysSessionSampleRate: 0,
    sendDefaultPii: false,
    tracesSampleRate: 0,
  });
  installBrowserErrorHandlers(window, capture, config);
}

export function reportFrameworkError(error: unknown) {
  if (!config.enabled) return;
  capture(error, {
    category: "web_runtime_error",
    environment: config.environment,
    releaseSha: config.releaseSha,
    route: typeof window === "undefined" ? "server_render" : safeRoute(window.location.href),
  });
}
