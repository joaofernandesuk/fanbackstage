import { describe, expect, it } from "vitest";

import {
  authErrorMessage,
  authEntryPath,
  authSuccessPath,
  clearRegistrationReturn,
  registrationReturn,
  rememberRegistrationReturn,
  safeAuthNextPath,
  safeAuthReturnPath,
} from "./auth-ui";

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

  it("carries a safe destination through email verification", () => {
    expect(authSuccessPath("register", "/messages")).toBe(
      "/verify-email?next=%2Fmessages",
    );
    expect(authSuccessPath("login", "/messages")).toBe("/messages");
  });

  it("builds deep-link fallbacks without creating an authentication loop", () => {
    expect(authEntryPath("login", "/creator/luna-sparks#subscriptions")).toBe(
      "/login?next=%2Fcreator%2Fluna-sparks%23subscriptions",
    );
    expect(authEntryPath("register", undefined)).toBe("/register?next=%2Fwelcome");
    expect(safeAuthReturnPath("/login?next=%2Fmessages", "/account")).toBe("/account");
  });

  it("persists only a sanitized registration return for the verification handoff", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      removeItem: (key: string) => { values.delete(key); },
      setItem: (key: string, value: string) => { values.set(key, value); },
    };

    expect(rememberRegistrationReturn(storage, "/creator/luna?tab=videos")).toBe(
      "/creator/luna?tab=videos",
    );
    expect(registrationReturn(storage)).toBe("/creator/luna?tab=videos");
    expect(registrationReturn(storage, "https://malicious.example")).toBe("/welcome");
    clearRegistrationReturn(storage);
    expect(registrationReturn(storage)).toBe("/welcome");
  });

  it("maps structured authentication failures to stable guidance", () => {
    expect(authErrorMessage("login", 401, "raw detail")).toBe(
      "The email address or password is incorrect.",
    );
    expect(
      authErrorMessage("login", 403, "Verify your email address before logging in."),
    ).toBe("Verify your email address before logging in.");
    expect(authErrorMessage("register", 422, [{ type: "missing" }])).toContain(
      "confirm you are at least 18",
    );
    expect(authErrorMessage("login", 500, "database detail")).not.toContain("database");
    expect(authErrorMessage("login", 418, "internal handler detail")).not.toContain(
      "internal handler detail",
    );
  });
});
