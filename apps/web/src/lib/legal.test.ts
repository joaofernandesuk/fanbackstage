import { describe, expect, it } from "vitest";

import {
  initialLegalAcceptanceSelection,
  legalBlocksToMarkdown,
  legalDocumentPath,
  legalGateBypasses,
  legalMarkdownToBlocks,
} from "./legal";

describe("legal content helpers", () => {
  it("turns supported plain Markdown into structured content without interpreting raw HTML", () => {
    const blocks = legalMarkdownToBlocks(
      "## Scope\n\n<script>alert('no')</script>\n\n- One\n- Two\n\n[Support](/legal/contact)",
    );
    expect(blocks).toEqual([
      { type: "heading", level: 2, text: "Scope" },
      { type: "paragraph", text: "<script>alert('no')</script>" },
      { type: "list", ordered: false, items: ["One", "Two"] },
      { type: "link", text: "Support", href: "/legal/contact" },
    ]);
    expect(legalBlocksToMarkdown(blocks)).toContain("<script>alert('no')</script>");
  });

  it("rejects non-HTTPS external links and credential-bearing URLs", () => {
    expect(() => legalMarkdownToBlocks("[Bad](javascript:evil)")).toThrow();
    expect(() => legalMarkdownToBlocks("[Bad](https://user:pass@example.com)"))
      .toThrow("Legal links");
    expect(() => legalMarkdownToBlocks("[Bad](/\\attacker.example)"))
      .toThrow("unsupported characters");
  });

  it("builds encoded public paths with explicit browsing scope", () => {
    expect(legalDocumentPath("fan terms", "pt", "pt-PT")).toBe(
      "/legal/fan%20terms?jurisdiction_code=PT&language=pt-PT",
    );
  });
});

describe("legal acceptance gate policy", () => {
  it("never preselects required versions", () => {
    expect([...initialLegalAcceptanceSelection(["v1", "v2"])]).toEqual([]);
  });

  it("keeps legal recovery and marketing opt-out reachable behind the global gate", () => {
    expect(legalGateBypasses("/legal/terms")).toBe(true);
    expect(legalGateBypasses("/account/legal")).toBe(true);
    expect(legalGateBypasses("/notification-settings")).toBe(true);
    expect(legalGateBypasses("/marketplace")).toBe(false);
    expect(legalGateBypasses("/admin/legal")).toBe(false);
  });
});
