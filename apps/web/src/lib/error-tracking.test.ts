import { describe, expect, it, vi } from "vitest";

import {
  browserErrorTrackingConfig,
  installBrowserErrorHandlers,
  safeRoute,
  scrubErrorEvent,
} from "./error-tracking";

class BrowserTargetStub {
  location = { href: "https://staging.example/live?token=private#fragment" };
  listeners = new Map<string, EventListener>();
  addEventListener(type: string, listener: EventListener) {
    this.listeners.set(type, listener);
  }
  removeEventListener(type: string) {
    this.listeners.delete(type);
  }
  dispatch(type: string, value: object) {
    this.listeners.get(type)?.(value as Event);
  }
}

describe("browser error tracking", () => {
  it("is a no-op without an explicitly configured Sentry exporter", () => {
    const target = new BrowserTargetStub();
    const transport = vi.fn();
    installBrowserErrorHandlers(target, transport, browserErrorTrackingConfig({}));
    target.dispatch("error", { error: new Error("handled login rejection") });
    expect(transport).not.toHaveBeenCalled();
  });

  it("does not instrument expected handled API failures", async () => {
    const target = new BrowserTargetStub();
    const transport = vi.fn();
    const config = browserErrorTrackingConfig({
      NEXT_PUBLIC_FANBACKSTAGE_ENVIRONMENT: "staging",
      NEXT_PUBLIC_FANBACKSTAGE_ERROR_TRACKING_PROVIDER: "sentry",
      NEXT_PUBLIC_FANBACKSTAGE_ERROR_TRACKING_DSN: "https://public@example.invalid/1",
      NEXT_PUBLIC_FANBACKSTAGE_RELEASE_SHA: "release-sha",
    });
    installBrowserErrorHandlers(target, transport, config);
    await Promise.reject(new Error("declined sandbox payment")).catch(() => undefined);
    expect(transport).not.toHaveBeenCalled();
  });

  it("captures unhandled errors and promise rejections with safe release context", () => {
    const target = new BrowserTargetStub();
    const transport = vi.fn();
    const config = browserErrorTrackingConfig({
      NEXT_PUBLIC_FANBACKSTAGE_ENVIRONMENT: "staging",
      NEXT_PUBLIC_FANBACKSTAGE_ERROR_TRACKING_PROVIDER: "sentry",
      NEXT_PUBLIC_FANBACKSTAGE_ERROR_TRACKING_DSN: "https://public@example.invalid/1",
      NEXT_PUBLIC_FANBACKSTAGE_RELEASE_SHA: "release-sha",
    });
    installBrowserErrorHandlers(target, transport, config);
    target.dispatch("error", { error: new Error("private value") });
    target.dispatch("unhandledrejection", { reason: new Error("private rejection") });
    expect(transport).toHaveBeenNthCalledWith(1, expect.any(Error), {
      category: "browser_unhandled_exception",
      environment: "staging",
      releaseSha: "release-sha",
      route: "/live",
    });
    expect(transport).toHaveBeenNthCalledWith(2, expect.any(Error), {
      category: "browser_unhandled_rejection",
      environment: "staging",
      releaseSha: "release-sha",
      route: "/live",
    });
  });

  it("strips request data, query strings, exception values, locals, and breadcrumbs", () => {
    const event = scrubErrorEvent({
      breadcrumbs: [{ message: "private message" }],
      exception: {
        values: [{ value: "token", stacktrace: { frames: [{ vars: { password: "x" } }] } }],
      },
      extra: { payment: "card" },
      message: "secret callback failure",
      request: { cookies: { session: "x" }, query_string: "code=secret" },
      tags: { "fanbackstage.category": "web_runtime_error", private: "secret" },
      threads: { values: [{ stacktrace: { frames: [{ vars: { token: "x" } }] } }] },
      user: { email: "person@example.com" },
    });
    expect(event).not.toHaveProperty("request");
    expect(event).not.toHaveProperty("user");
    expect(event).not.toHaveProperty("breadcrumbs");
    expect(event).not.toHaveProperty("extra");
    expect(event.message).toBe("[redacted]");
    expect(event.tags).toEqual({ "fanbackstage.category": "web_runtime_error" });
    expect(JSON.stringify(event)).not.toMatch(/token|password|private message|person@example/);
    expect(safeRoute("https://example.com/path?code=secret#value")).toBe("/path");
  });

  it("bounds repeated browser crashes", () => {
    const target = new BrowserTargetStub();
    const transport = vi.fn();
    installBrowserErrorHandlers(
      target,
      transport,
      browserErrorTrackingConfig({
        NEXT_PUBLIC_FANBACKSTAGE_ENVIRONMENT: "staging",
        NEXT_PUBLIC_FANBACKSTAGE_ERROR_TRACKING_PROVIDER: "sentry",
        NEXT_PUBLIC_FANBACKSTAGE_ERROR_TRACKING_DSN: "https://public@example.invalid/1",
        NEXT_PUBLIC_FANBACKSTAGE_RELEASE_SHA: "release-sha",
      }),
    );
    for (let attempt = 0; attempt < 10; attempt += 1) {
      target.dispatch("error", { error: new Error("repeated") });
    }
    expect(transport).toHaveBeenCalledTimes(5);
  });
});
