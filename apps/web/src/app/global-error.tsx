"use client";

import { useEffect } from "react";

import { reportFrameworkError } from "../lib/sentry-browser";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => reportFrameworkError(error), [error]);
  return (
    <html lang="en">
      <body>
        <main className="card">
          <p className="eyebrow">SOMETHING WENT WRONG</p>
          <h1>FanBackstage could not finish that page.</h1>
          <p>The failure was reported without your private account or request data.</p>
          <button onClick={reset} type="button">
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
