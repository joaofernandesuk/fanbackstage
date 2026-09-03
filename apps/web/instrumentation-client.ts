import * as Sentry from "@sentry/nextjs";

import { initializeBrowserErrorTracking } from "./src/lib/sentry-browser";

initializeBrowserErrorTracking();

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
