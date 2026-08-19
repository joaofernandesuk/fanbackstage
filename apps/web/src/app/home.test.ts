import { describe, expect, it } from "vitest";

describe("FanBackstage web foundation", () => {
  it("uses the production product name", () => {
    expect("FanBackstage — Get closer. Go backstage.").toContain("FanBackstage");
  });
});

