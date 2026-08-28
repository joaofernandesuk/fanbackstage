import { describe, expect, it } from "vitest";

import robots from "./robots";
import sitemap from "./sitemap";

describe("public SEO projections", () => {
  it("never enumerates personalized or protected routes", () => {
    const urls = sitemap().map((entry) => entry.url);
    expect(urls.some((url) => /account|admin|creator-studio|messages|purchases/.test(url))).toBe(false);
    expect(urls.some((url) => /\/creator\/|\/content\//.test(url))).toBe(false);
  });

  it("asks crawlers not to index authenticated surfaces", () => {
    const rules = robots().rules;
    expect(Array.isArray(rules)).toBe(false);
    if (Array.isArray(rules)) return;
    expect(rules.disallow).toContain("/account/");
    expect(rules.disallow).toContain("/messages/");
    expect(rules.disallow).toContain("/creator-studio/");
  });
});
