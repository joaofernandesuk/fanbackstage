export type LegalAudience = "all_users" | "fan" | "creator" | "group_manager" | "affiliate";
export type LegalDocumentStatus = "draft" | "published" | "retired";
export type LegalDocumentType =
  | "terms"
  | "privacy"
  | "cookies"
  | "community_guidelines"
  | "acceptable_use"
  | "prohibited_content"
  | "creator_agreement"
  | "fan_terms"
  | "refund_policy"
  | "marketplace_terms"
  | "live_rules"
  | "age_policy"
  | "copyright"
  | "complaints"
  | "appeals"
  | "performer_consent"
  | "contact_support"
  | "record_keeping_notice";

export type LegalBodyBlock =
  | { type: "heading"; level: 2 | 3 | 4; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "callout"; text: string }
  | { type: "link"; text: string; href: string };

export type LegalDocument = {
  document_id: string;
  version_id: string;
  document_type: LegalDocumentType;
  title: string;
  slug: string;
  jurisdiction_code: string | null;
  language: string;
  audience: LegalAudience;
  version: number;
  status: LegalDocumentStatus;
  body: LegalBodyBlock[];
  effective_from: string | null;
  effective_until: string | null;
  requires_acceptance: boolean;
  requires_legal_review: boolean;
  approved_for_publication: boolean;
  is_demo: boolean;
  published_at: string | null;
};

export type LegalDocumentSummary = Omit<LegalDocument, "body"> & { created_at: string };

export type LegalDocumentDetail = {
  document_id: string;
  document_type: LegalDocumentType;
  slug: string;
  jurisdiction_code: string | null;
  language: string;
  audience: LegalAudience;
  versions: LegalDocumentSummary[];
};

export type LegalDocumentPage = {
  items: LegalDocumentSummary[];
  total: number;
  limit: number;
  offset: number;
};

export type LegalAcceptance = {
  acceptance_id: string;
  version_id: string;
  document_type: LegalDocumentType;
  title: string;
  version: number;
  jurisdiction_code: string | null;
  source: string;
  accepted_at: string;
};

export type SiteSocialLink = { label: string; url: string };

export type SiteSettings = {
  version: number;
  support_email: string | null;
  footer_text: string | null;
  public_contact_text: string | null;
  social_links: SiteSocialLink[];
  homepage_announcement: string | null;
  maintenance_notice: string | null;
  banner_level: "info" | "warning" | "critical";
  banner_starts_at: string | null;
  banner_ends_at: string | null;
  banner_active: boolean;
  updated_at: string | null;
};

export const LEGAL_DOCUMENT_TYPES: LegalDocumentType[] = [
  "terms",
  "privacy",
  "cookies",
  "community_guidelines",
  "acceptable_use",
  "prohibited_content",
  "creator_agreement",
  "fan_terms",
  "refund_policy",
  "marketplace_terms",
  "live_rules",
  "age_policy",
  "copyright",
  "complaints",
  "appeals",
  "performer_consent",
  "contact_support",
  "record_keeping_notice",
];

export function legalDocumentPath(slug: string, jurisdiction?: string | null, language = "en") {
  const query = new URLSearchParams();
  if (jurisdiction) query.set("jurisdiction_code", jurisdiction.toUpperCase());
  if (language !== "en") query.set("language", language);
  const suffix = query.size ? `?${query.toString()}` : "";
  return `/legal/${encodeURIComponent(slug)}${suffix}`;
}

function safeLegalHref(href: string) {
  if (href.includes("\\") || /[\u0000-\u001f]/.test(href)) {
    throw new Error("Legal links contain unsupported characters.");
  }
  if (href.startsWith("/") && !href.startsWith("//")) return href;
  const parsed = new URL(href);
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
    throw new Error("Legal links must be internal paths or HTTPS URLs.");
  }
  return parsed.toString();
}

export function legalMarkdownToBlocks(markdown: string): LegalBodyBlock[] {
  const lines = markdown.replaceAll("\r\n", "\n").split("\n");
  const blocks: LegalBodyBlock[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = () => {
    const text = paragraph.join("\n").trim();
    if (text) blocks.push({ type: "paragraph", text });
    paragraph = [];
  };
  const flushList = () => {
    if (list?.items.length) blocks.push({ type: "list", ...list });
    list = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const heading = /^(#{2,4})\s+(.+)$/.exec(line);
    const unordered = /^[-*]\s+(.+)$/.exec(line);
    const ordered = /^\d+[.)]\s+(.+)$/.exec(line);
    const callout = /^>\s?(.+)$/.exec(line);
    const link = /^\[([^\]]+)]\(([^)]+)\)$/.exec(line.trim());

    if (!line.trim()) {
      flushParagraph();
      flushList();
    } else if (heading) {
      flushParagraph();
      flushList();
      blocks.push({
        type: "heading",
        level: heading[1].length as 2 | 3 | 4,
        text: heading[2].trim(),
      });
    } else if (unordered || ordered) {
      flushParagraph();
      const nextOrdered = Boolean(ordered);
      if (list && list.ordered !== nextOrdered) flushList();
      list ??= { ordered: nextOrdered, items: [] };
      list.items.push((unordered?.[1] ?? ordered?.[1] ?? "").trim());
    } else if (callout) {
      flushParagraph();
      flushList();
      blocks.push({ type: "callout", text: callout[1].trim() });
    } else if (link) {
      flushParagraph();
      flushList();
      blocks.push({ type: "link", text: link[1].trim(), href: safeLegalHref(link[2].trim()) });
    } else {
      flushList();
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushList();
  if (!blocks.length) throw new Error("Legal content cannot be empty.");
  return blocks;
}

export function legalBlocksToMarkdown(blocks: LegalBodyBlock[]) {
  return blocks.map((block) => {
    if (block.type === "heading") return `${"#".repeat(block.level)} ${block.text}`;
    if (block.type === "paragraph") return block.text;
    if (block.type === "callout") return `> ${block.text}`;
    if (block.type === "link") return `[${block.text}](${block.href})`;
    return block.items
      .map((item, index) => block.ordered ? `${index + 1}. ${item}` : `- ${item}`)
      .join("\n");
  }).join("\n\n");
}

export function displayLegalType(value: string) {
  return value.replaceAll("_", " ").replace(/(^|\s)\S/g, (letter) => letter.toUpperCase());
}

export function initialLegalAcceptanceSelection(_requiredVersionIds: readonly string[]) {
  return new Set<string>();
}

export function reconcileLegalAcceptanceSelection(
  current: ReadonlySet<string>,
  requiredVersionIds: readonly string[],
) {
  const required = new Set(requiredVersionIds);
  return new Set([...current].filter((versionId) => required.has(versionId)));
}

export function legalGateBypasses(pathname: string) {
  return pathname === "/legal"
    || pathname.startsWith("/legal/")
    || pathname === "/account/legal"
    || pathname.startsWith("/account/legal/")
    || pathname === "/notification-settings";
}
