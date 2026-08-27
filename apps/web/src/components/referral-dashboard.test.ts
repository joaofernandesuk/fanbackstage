import { describe, expect, it } from "vitest";

import { type Allocation, referralAllocationStatus } from "./referral-dashboard";

function allocation(overrides: Partial<Allocation> = {}): Allocation {
  return {
    id: "allocation-1",
    revenue_type: "marketplace",
    currency: "EUR",
    amount_minor: 25,
    allocated_at: "2026-08-27T10:00:00Z",
    released_at: "2026-08-27T11:00:00Z",
    reversed_at: null,
    availability_status: "available",
    ...overrides,
  };
}

describe("referral allocation projection", () => {
  it("renders an actively held released allocation as pending from authoritative status", () => {
    expect(referralAllocationStatus(allocation({ availability_status: "pending" }))).toBe(
      "pending",
    );
  });

  it("renders restored and terminally reversed allocations from authoritative status", () => {
    expect(referralAllocationStatus(allocation({ availability_status: "available" }))).toBe(
      "available",
    );
    expect(
      referralAllocationStatus(
        allocation({
          availability_status: "reversed",
          reversed_at: "2026-08-27T12:00:00Z",
        }),
      ),
    ).toBe("reversed");
  });
});
