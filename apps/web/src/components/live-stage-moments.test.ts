import { describe, expect, it } from "vitest";

import { effectForActivity } from "./live-stage-moments";

describe("Live stage moments", () => {
  it("turns only canonical settled commerce into financial effects", () => {
    expect(effectForActivity({
      id: "tip-1",
      event_type: "tip",
      amount_minor: 250,
      currency: "EUR",
      metadata: { tip_label: "You look amazing", tip_icon: "/tip.svg" },
    })).toMatchObject({ kind: "tip", title: "You look amazing", detail: "€2.50" });
    expect(effectForActivity({
      id: "reversal-1",
      event_type: "commerce_reversed",
      amount_minor: 250,
      currency: "EUR",
      metadata: {},
    })).toBeNull();
  });

  it("presents gifts, paid requests, snapshots, and goals with authoritative metadata", () => {
    expect(effectForActivity({ id: "gift-1", event_type: "gift", amount_minor: 300, currency: "EUR", metadata: { gift_name: "Rose" } })?.title).toBe("Rose");
    expect(effectForActivity({ id: "request-1", event_type: "paid_request_pending", amount_minor: null, currency: null, metadata: { request_label: "Encore" } })?.detail).toBe("Waiting for the creator");
    expect(effectForActivity({ id: "snapshot-1", event_type: "snapshot", amount_minor: 425, currency: "EUR", metadata: {} })).toMatchObject({ kind: "snapshot", detail: "€4.25" });
    expect(effectForActivity({ id: "vip-1", event_type: "vip_admission", amount_minor: 500, currency: "EUR", metadata: {} })).toMatchObject({ kind: "vip", detail: "€5.00" });
    expect(effectForActivity({ id: "goal-1", event_type: "goal_completed", amount_minor: null, currency: null, metadata: { title: "First goal" } })?.title).toBe("First goal");
  });
});
