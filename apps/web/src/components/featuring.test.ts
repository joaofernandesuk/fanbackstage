import { describe, expect, it } from "vitest";

import { ApiError } from "../lib/api";
import {
  featuringAttemptKey,
  featuringPaymentAction,
  featuringPaymentError,
} from "./featuring";

describe("Featuring payment UX", () => {
  it("replays one request key until the user deliberately retries a failed attempt", () => {
    const first = featuringAttemptKey(null, "booking-1", false, () => "first-key");
    const safeReplay = featuringAttemptKey(first, "booking-1", false, () => "unused-key");
    const deliberateRetry = featuringAttemptKey(first, "booking-1", true, () => "retry-key");

    expect(safeReplay).toBe(first);
    expect(deliberateRetry).toEqual({ bookingId: "booking-1", key: "retry-key" });
  });

  it("offers retry only while a failed reservation is still authoritative", () => {
    expect(featuringPaymentAction({ status: "failed", retryable: true })).toBe("Retry payment");
    expect(featuringPaymentAction({ status: "failed", retryable: false })).toBeNull();
    expect(featuringPaymentAction({ status: "awaiting_payment", retryable: false })).toBeNull();
    expect(featuringPaymentAction({ status: "scheduled", retryable: false })).toBeNull();
  });

  it("maps provider and server failures to safe inline guidance", () => {
    expect(featuringPaymentError(new ApiError("private SQL detail", 500))).not.toContain("SQL");
    expect(featuringPaymentError(new ApiError("Booking payment reservation has expired", 400)))
      .toBe("This slot reservation expired before payment completed. Create a new booking.");
  });
});
