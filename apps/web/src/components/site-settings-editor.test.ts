import { describe, expect, it } from "vitest";

import { parseSocialLinks } from "./site-settings-editor";

describe("site settings social links", () => {
  it("parses explicit labels and HTTPS URLs", () => {
    expect(parseSocialLinks("Instagram | https://instagram.com/fanbackstage\n"))
      .toEqual([{ label: "Instagram", url: "https://instagram.com/fanbackstage" }]);
  });

  it("rejects insecure and credential-bearing links before mutation", () => {
    expect(() => parseSocialLinks("Example | http://example.com")).toThrow("HTTPS");
    expect(() => parseSocialLinks("Example | https://user:secret@example.com"))
      .toThrow("HTTPS");
  });
});
