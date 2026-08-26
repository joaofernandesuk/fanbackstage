import { describe, expect, it } from "vitest";

import {
  accessLabel,
  creatorUsernameFor,
  discoverySearchPath,
  formatMoney,
  groupDiscovery,
  previewUrl,
  type DiscoveryResult,
} from "./public-api";

const result = (overrides: Partial<DiscoveryResult> = {}): DiscoveryResult => ({
  entity_type: "creator",
  id: "00000000-0000-0000-0000-000000000001",
  title: "Luna Sparks",
  subtitle: "@luna-sparks",
  creator_id: "00000000-0000-0000-0000-000000000001",
  creator_username: "luna-sparks",
  locked: false,
  live: false,
  created_at: "2026-08-26T00:00:00Z",
  placement_type: "organic",
  sponsored: false,
  ...overrides,
});

describe("public consumer API helpers", () => {
  it("serializes repeated discovery entity types without changing API semantics", () => {
    const path = discoverySearchPath({ query: "Luna Sparks", types: ["creator", "video"], liveNow: true, limit: 12 });
    const query = new URLSearchParams(path.split("?")[1]);
    expect(query.get("q")).toBe("Luna Sparks");
    expect(query.getAll("types")).toEqual(["creator", "video"]);
    expect(query.get("live_now")).toBe("true");
    expect(query.get("limit")).toBe("12");
  });

  it("formats integer minor units with the currency exponent", () => {
    expect(formatMoney(999, "EUR")).toBe("€9.99");
    expect(formatMoney(1250, "JPY")).toBe("JP¥1,250");
    expect(formatMoney(-99, "EUR")).toBe("-€0.99");
  });

  it("uses explicit consumer access labels", () => {
    expect(accessLabel("free", false)).toBe("FREE");
    expect(accessLabel("subscription", true)).toBe("SUBSCRIBERS");
    expect(accessLabel("ppv", true)).toBe("PREMIUM / PPV");
    expect(accessLabel("ppv", false)).toBe("PREMIUM / PPV");
    expect(accessLabel("private", true)).toBe("PRIVATE");
  });

  it("never builds a preview route without an authorised derivative id", () => {
    expect(previewUrl(null)).toBeUndefined();
    expect(previewUrl("derivative-id")).toMatch(/\/api\/v1\/media\/previews\/derivative-id$/);
  });

  it("groups only server-live results as live", () => {
    const items = [result(), result({ id: "2", entity_type: "video" }), result({ id: "3", live: true })];
    expect(groupDiscovery(items).live.map((item) => item.id)).toEqual(["3"]);
    expect(creatorUsernameFor(result({ creator_username: null }))).toBe("luna-sparks");
  });
});
