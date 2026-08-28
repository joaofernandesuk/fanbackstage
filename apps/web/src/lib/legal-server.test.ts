import { createHmac } from "node:crypto";
import { afterEach, describe, expect, it } from "vitest";

import { signedCountryHandoffHeaders } from "./legal-server";

describe("legal SSR country handoff", () => {
  afterEach(() => {
    delete process.env.FANBACKSTAGE_INTERNAL_COUNTRY_HANDOFF_SECRET;
  });

  it("signs only normalized edge countries and binds the API pathname", () => {
    const secret = "internal-country-handoff-test-secret-123456";
    process.env.FANBACKSTAGE_INTERNAL_COUNTRY_HANDOFF_SECRET = secret;
    const timestamp = 1_800_000_000;
    const result = signedCountryHandoffHeaders(
      "/legal/documents?language=en",
      " gb ",
      timestamp,
    );
    expect(result["X-FanBackstage-Internal-Country"]).toBe("GB");
    expect(result["X-FanBackstage-Internal-Country-Timestamp"]).toBe(String(timestamp));
    expect(result["X-FanBackstage-Internal-Country-Signature"]).toBe(
      createHmac("sha256", secret)
        .update(`GB\n${timestamp}\n/api/v1/legal/documents`)
        .digest("hex"),
    );
    expect(signedCountryHandoffHeaders("/legal/documents", "GBR", timestamp)).toEqual({});
  });
});
