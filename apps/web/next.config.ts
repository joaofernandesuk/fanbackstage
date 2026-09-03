import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs/config";

const privateStaging = process.env.FANBACKSTAGE_ENVIRONMENT === "staging";
const sharedEnvironment = ["staging", "production"].includes(
  process.env.FANBACKSTAGE_ENVIRONMENT ?? "development",
);

if (sharedEnvironment) {
  const browserDsn = process.env.NEXT_PUBLIC_FANBACKSTAGE_ERROR_TRACKING_DSN ?? "";
  const browserEnvironment = process.env.NEXT_PUBLIC_FANBACKSTAGE_ENVIRONMENT;
  const releaseSha = process.env.NEXT_PUBLIC_FANBACKSTAGE_RELEASE_SHA ?? "";
  let validDsn = false;
  try {
    const parsed = new URL(browserDsn);
    validDsn =
      parsed.protocol === "https:" &&
      Boolean(parsed.username && parsed.hostname && parsed.pathname.replaceAll("/", "")) &&
      !["localhost", "127.0.0.1", "::1"].includes(parsed.hostname) &&
      !parsed.password &&
      !parsed.search &&
      !parsed.hash;
  } catch {}
  if (
    process.env.NEXT_PUBLIC_FANBACKSTAGE_ERROR_TRACKING_PROVIDER !== "sentry" ||
    !validDsn ||
    browserEnvironment !== process.env.FANBACKSTAGE_ENVIRONMENT ||
    releaseSha === "development" ||
    releaseSha.length < 7
  ) {
    throw new Error("Shared web builds require safe Sentry environment, DSN, and release values.");
  }
}

const nextConfig: NextConfig = {
  output: "standalone",
  async headers() {
    if (!privateStaging) return [];
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Robots-Tag", value: "noindex, nofollow, noarchive" },
          { key: "Cache-Control", value: "private, no-store" },
        ],
      },
    ];
  },
};

const sourceMapUploadConfigured = Boolean(
  process.env.SENTRY_AUTH_TOKEN && process.env.SENTRY_ORG && process.env.SENTRY_PROJECT,
);

export default sourceMapUploadConfigured
  ? withSentryConfig(nextConfig, {
      authToken: process.env.SENTRY_AUTH_TOKEN,
      org: process.env.SENTRY_ORG,
      project: process.env.SENTRY_PROJECT,
      release: { name: process.env.NEXT_PUBLIC_FANBACKSTAGE_RELEASE_SHA },
      silent: true,
      sourcemaps: { deleteSourcemapsAfterUpload: true },
      webpack: { treeshake: { removeDebugLogging: true } },
    })
  : nextConfig;
