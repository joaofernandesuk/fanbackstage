import { describe, expect, it } from "vitest";

import {
  subscriptionAttemptKey,
  subscriptionPaymentRequiresNewKey,
} from "./subscription-options";

describe("subscription confirmation idempotency", () => {
  it("reuses the same key for a safe retry of the same duration", () => {
    const first = subscriptionAttemptKey(null, "month_3", () => "first-key");
    const retry = subscriptionAttemptKey(first, "month_3", () => "must-not-rotate");
    expect(retry).toBe(first);
    expect(retry.key).toBe("first-key");
  });

  it("rotates only when the buyer deliberately chooses another duration", () => {
    const first = subscriptionAttemptKey(null, "month_3", () => "first-key");
    const changed = subscriptionAttemptKey(first, "month_6", () => "second-key");
    expect(changed).toEqual({ duration: "month_6", key: "second-key" });
  });

  it("rotates only after the server identifies canonical payment failure", () => {
    expect(subscriptionPaymentRequiresNewKey("payment_failed")).toBe(true);
    expect(subscriptionPaymentRequiresNewKey("pending")).toBe(false);
    expect(subscriptionPaymentRequiresNewKey("active")).toBe(false);
  });
});
