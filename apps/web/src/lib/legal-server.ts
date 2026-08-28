import { createHmac } from "node:crypto";
import { cookies, headers as requestHeaders } from "next/headers";

import type { LegalDocument, SiteSettings } from "./legal";

const baseUrl = process.env.FANBACKSTAGE_API_INTERNAL_URL
  ?? process.env.NEXT_PUBLIC_FANBACKSTAGE_API_URL
  ?? "http://localhost:8000";

const internalCountryHeader = "X-FanBackstage-Internal-Country";
const internalCountryTimestampHeader = "X-FanBackstage-Internal-Country-Timestamp";
const internalCountrySignatureHeader = "X-FanBackstage-Internal-Country-Signature";

export function signedCountryHandoffHeaders(
  path: string,
  countryValue: string | null,
  timestamp = Math.floor(Date.now() / 1000),
): Record<string, string> {
  const secret = process.env.FANBACKSTAGE_INTERNAL_COUNTRY_HANDOFF_SECRET?.trim();
  const country = countryValue?.trim().toUpperCase() ?? "";
  if (!secret || !/^[A-Z]{2}$/.test(country)) return {};
  const pathname = path.split("?", 1)[0];
  const signature = createHmac("sha256", secret)
    .update(`${country}\n${timestamp}\n/api/v1${pathname}`)
    .digest("hex");
  return {
    [internalCountryHeader]: country,
    [internalCountryTimestampHeader]: String(timestamp),
    [internalCountrySignatureHeader]: signature,
  };
}

async function publicFetch<T>(path: string): Promise<T | null> {
  const cookieStore = await cookies();
  const incomingHeaders = await requestHeaders();
  const edgeCountryHeader = process.env.FANBACKSTAGE_TRUSTED_COUNTRY_HEADER?.trim();
  const country = edgeCountryHeader ? incomingHeaders.get(edgeCountryHeader) : null;
  const response = await fetch(`${baseUrl}/api/v1${path}`, {
    cache: "no-store",
    headers: {
      cookie: cookieStore.toString(),
      ...signedCountryHandoffHeaders(path, country),
    },
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Public legal API returned ${response.status}`);
  return response.json() as Promise<T>;
}

export const serverLegalDocument = async (
  slug: string,
  jurisdictionCode = "",
  language = "en",
) => {
  const query = new URLSearchParams({ language });
  if (jurisdictionCode) query.set("jurisdiction_code", jurisdictionCode);
  return publicFetch<LegalDocument>(
    `/legal/documents/${encodeURIComponent(slug)}?${query.toString()}`,
  );
};

export async function serverLegalDocuments() {
  return (await publicFetch<LegalDocument[]>("/legal/documents")) ?? [];
}

export async function serverSiteSettings(): Promise<SiteSettings | null> {
  return publicFetch<SiteSettings>("/site-settings/public");
}
