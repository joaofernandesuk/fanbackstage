import { describe, expect, it } from "vitest";

import {
  contentDeliveryUrl,
  contentDetailPath,
  marketplaceListingMediaUrl,
} from "./content-api";

describe("content API helpers", () => {
  it("keeps content routes internal and encoded", () => {
    expect(contentDetailPath("gallery/id")).toBe("/content/gallery%2Fid");
  });

  it("accepts only server-issued media delivery paths", () => {
    expect(contentDeliveryUrl("/media/previews/preview-id")).toContain(
      "/api/v1/media/previews/preview-id",
    );
    expect(() => contentDeliveryUrl("https://attacker.example/original.mp4")).toThrow(
      "Invalid content media path",
    );
    expect(() => contentDeliveryUrl("/media/../../original/private.mp4")).toThrow(
      "Invalid content media path",
    );
    expect(() => contentDeliveryUrl("/media/previews/id?redirect=/original")).toThrow(
      "Invalid content media path",
    );
  });

  it("selects the first authoritative marketplace derivative and fails closed", () => {
    expect(marketplaceListingMediaUrl([
      { derivative_id: "second", delivery_path: "/media/previews/second", position: 1, width: 800, height: 600 },
      { derivative_id: "first", delivery_path: "/media/previews/first", position: 0, width: 800, height: 600 },
    ])).toContain("/api/v1/media/previews/first");
    expect(marketplaceListingMediaUrl([
      { derivative_id: "bad", delivery_path: "/media/../../private", position: 0, width: null, height: null },
    ])).toBeUndefined();
  });
});
