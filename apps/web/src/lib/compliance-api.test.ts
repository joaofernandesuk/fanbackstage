import { describe, expect, it } from "vitest";

import { currentSafeReturnPath, safeProviderAuthorizationUrl } from "./compliance-api";

describe("compliance browser helpers", () => {
  it("keeps only an internal return path", () => {
    expect(currentSafeReturnPath({ pathname: "/content/one", search: "?tab=media" })).toBe(
      "/content/one?tab=media",
    );
    expect(currentSafeReturnPath({ pathname: "//malicious.example", search: "" })).toBe("/");
  });

  it("accepts secure provider redirects and loopback HTTP only", () => {
    expect(safeProviderAuthorizationUrl("https://verify.example/start")?.startsWith("https://")).toBe(true);
    expect(safeProviderAuthorizationUrl("http://localhost:18000/test/start")).toBe(
      "http://localhost:18000/test/start",
    );
    expect(safeProviderAuthorizationUrl("http://127.0.0.1:18000/test/start")).toBe(
      "http://127.0.0.1:18000/test/start",
    );
    expect(safeProviderAuthorizationUrl("http://verify.example/start")).toBeNull();
    expect(safeProviderAuthorizationUrl("javascript:alert(1)")).toBeNull();
    expect(safeProviderAuthorizationUrl("/relative/provider")).toBeNull();
  });
});
