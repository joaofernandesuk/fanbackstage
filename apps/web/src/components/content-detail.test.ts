import { describe, expect, it } from "vitest";

import {
  purchaseAttemptKey,
  purchasePaymentRequiresNewKey,
  relatedCreatorContent,
} from "./content-detail";
import type { PublicContent } from "../lib/content-api";

describe("PPV purchase idempotency", () => {
  it("rotates only after a canonical failed response", () => {
    expect(purchasePaymentRequiresNewKey("failed")).toBe(true);
    expect(purchasePaymentRequiresNewKey("awaiting_payment")).toBe(false);
    expect(purchasePaymentRequiresNewKey("paid")).toBe(false);
  });

  it("stores and reuses the replacement key across ambiguous retries", () => {
    const replacement = purchaseAttemptKey(null, () => "replacement-key");
    expect(replacement).toBe("replacement-key");
    expect(purchaseAttemptKey(replacement, () => "must-not-rotate")).toBe(
      "replacement-key",
    );
  });
});

describe("related creator content", () => {
  it("uses only the ordered authoritative creator projection and excludes the current item", () => {
    const item = (id: string): PublicContent => ({
      id,
      content_type: "gallery",
      title: `Release ${id}`,
      description: null,
      status: "published",
      access_policy: "free",
      has_access: true,
      locked: false,
      price_amount_minor: null,
      price_currency: null,
      requires_verified_consent: false,
      creator_id: "creator-id",
      creator_username: "luna",
      creator_display_name: "Luna",
      published_at: null,
      media_count: 1,
      duration_seconds: null,
      previews: [],
      media: [],
    });

    expect(relatedCreatorContent("current", [
      item("first"),
      item("current"),
      item("second"),
      item("third"),
    ], 2).map((content) => content.id)).toEqual(["first", "second"]);
  });
});
