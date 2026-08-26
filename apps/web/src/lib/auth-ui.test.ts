import { describe, expect, it } from "vitest";

import { authSuccessPath, safeAuthNextPath } from "./auth-ui";

describe("authentication UI navigation", () => {
  it("preserves an internal deep link after login", () => {
    expect(safeAuthNextPath("/messages?conversation=one#latest")).toBe(
      "/messages?conversation=one#latest",
    );
  });

  it("rejects external and scheme-relative redirects", () => {
    expect(safeAuthNextPath("https://malicious.example/collect")).toBe("/account");
    expect(safeAuthNextPath("//malicious.example/collect")).toBe("/account");
    expect(safeAuthNextPath("/\\malicious.example/collect")).toBe("/account");
  });

  it("always sends a new account to email verification", () => {
    expect(authSuccessPath("register", "/messages")).toBe("/verify-email");
    expect(authSuccessPath("login", "/messages")).toBe("/messages");
  });
});
