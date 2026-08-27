import { describe, expect, it } from "vitest";

import { ApiError } from "../lib/api";
import {
  marketplaceCheckoutError,
  marketplaceCheckoutRequiresNewKey,
  marketplaceCheckoutPayload,
} from "./marketplace-detail";

describe("marketplace checkout UI", () => {
  it("builds the authoritative checkout payload from reviewed delivery details", () => {
    expect(marketplaceCheckoutPayload({
      quantity: 2,
      recipientName: "  Demo Buyer ",
      line1: " 1 Creator Street ",
      line2: "",
      city: " Lisbon ",
      regionCode: " lis ",
      postalCode: " 1000-001 ",
      countryCode: " pt ",
    })).toEqual({
      quantity: 2,
      destination_country_code: "PT",
      destination_region_code: "LIS",
      shipping_address: {
        recipient_name: "Demo Buyer",
        line1: "1 Creator Street",
        city: "Lisbon",
        region_code: "LIS",
        postal_code: "1000-001",
        country_code: "PT",
      },
    });
  });

  it("omits optional blank delivery fields", () => {
    const payload = marketplaceCheckoutPayload({
      quantity: 1,
      recipientName: "Buyer",
      line1: "Street",
      line2: "   ",
      city: "Porto",
      regionCode: "",
      postalCode: "4000",
      countryCode: "PT",
    });

    expect(payload).not.toHaveProperty("destination_region_code");
    expect(payload.shipping_address).not.toHaveProperty("line2");
    expect(payload.shipping_address).not.toHaveProperty("region_code");
  });

  it("does not expose unknown server details in checkout errors", () => {
    expect(marketplaceCheckoutError(new ApiError("private database detail", 500)))
      .not.toContain("database");
    expect(marketplaceCheckoutError(new ApiError("stock internals", 409)))
      .toBe("Listing availability changed during checkout. Refresh the listing before retrying.");
  });

  it("rotates the checkout key only after a canonical terminal payment response", () => {
    const terminal = new ApiError("Payment failed", 409, "marketplace_payment_terminal");
    expect(marketplaceCheckoutRequiresNewKey(terminal)).toBe(true);
    expect(marketplaceCheckoutError(terminal))
      .toBe("Payment was not completed. Retry to create a new, stock-checked order.");
    expect(marketplaceCheckoutRequiresNewKey(new ApiError("Transport failed", 500)))
      .toBe(false);
    expect(marketplaceCheckoutRequiresNewKey(new ApiError("Stock changed", 409)))
      .toBe(false);
  });
});
