import * as Sentry from "@sentry/nextjs";
import type { Instrumentation } from "next";

import { safeRoute } from "./src/lib/error-tracking";

export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") await import("./sentry.server.config");
  if (process.env.NEXT_RUNTIME === "edge") await import("./sentry.edge.config");
}

export const onRequestError: Instrumentation.onRequestError = (error, request, context) => {
  Sentry.withScope((scope) => {
    scope.setTag("fanbackstage.category", "web_runtime_error");
    scope.setContext("fanbackstage", {
      environment: process.env.FANBACKSTAGE_ENVIRONMENT,
      method: request.method,
      release_sha: process.env.FANBACKSTAGE_RELEASE_SHA,
      route: safeRoute(request.path),
      route_type: context.routeType,
    });
    Sentry.captureRequestError(error, request, context);
  });
};
