import { describe, expect, it } from "vitest";

import { complianceGateMode } from "./adult-access-gate";

describe("complianceGateMode", () => {
  it("starts provider verification only when the server requests VERIFY_AGE", () => {
    expect(complianceGateMode("VERIFY_AGE")).toBe("verify");
    expect(complianceGateMode("LOGIN")).toBe("login");
  });

  it("does not treat unresolved or support states as provider-start actions", () => {
    expect(complianceGateMode("CONTACT_SUPPORT")).toBe("retry");
    expect(complianceGateMode("RETRY_LATER")).toBe("retry");
    expect(complianceGateMode(null)).toBe("retry");
  });
});
