import * as Sentry from "@sentry/nextjs";

import { scrubErrorEvent } from "./src/lib/error-tracking";

if (process.env.FANBACKSTAGE_ERROR_TRACKING_PROVIDER === "sentry") {
  Sentry.init({
    beforeSend: (event) => scrubErrorEvent(event),
    defaultIntegrations: false,
    dsn: process.env.FANBACKSTAGE_ERROR_TRACKING_DSN,
    environment: process.env.FANBACKSTAGE_ENVIRONMENT,
    includeLocalVariables: false,
    maxBreadcrumbs: 0,
    release: process.env.FANBACKSTAGE_RELEASE_SHA,
    sendDefaultPii: false,
    tracesSampleRate: 0,
  });
}
