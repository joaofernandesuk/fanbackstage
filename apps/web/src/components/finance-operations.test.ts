import { describe, expect, it } from "vitest";

import { formatMoney } from "./finance-operations";

describe("finance operations presentation", () => {
  it("renders integer minor units with an explicit currency", () => {
    expect(formatMoney(1299, "EUR")).toMatch(/12[.,]99/);
    expect(formatMoney(1299, "EUR")).toMatch(/€|EUR/);
  });
});
