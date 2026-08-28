import { api } from "./api";
import type { ComplianceAccess } from "./compliance-api";

export type DiscoveryEntityType =
  | "creator"
  | "post"
  | "video"
  | "gallery"
  | "marketplace_listing"
  | "live_room";

export type DiscoveryResult = Partial<ComplianceAccess> & {
  entity_type: DiscoveryEntityType;
  id: string;
  title: string;
  subtitle?: string | null;
  description?: string | null;
  creator_id?: string | null;
  creator_username?: string | null;
  access_policy?: string | null;
  locked: boolean;
  preview_asset_id?: string | null;
  gallery_image_count?: number | null;
  video_duration_seconds?: number | null;
  adult_access_required?: boolean;
  adult_access_granted?: boolean;
  price_amount_minor?: number | null;
  currency?: string | null;
  availability?: string | null;
  live: boolean;
  started_at?: string | null;
  created_at: string;
  reason?: string | null;
  placement_type: string;
  sponsored: boolean;
  sponsored_surface?: string | null;
};

export type DiscoveryPage = Partial<ComplianceAccess> & {
  items: DiscoveryResult[];
  next_cursor: string | null;
  ranking_version: number;
};

export type PublicCreator = ComplianceAccess & {
  id: string;
  username: string;
  display_name: string;
  bio: string | null;
  avatar_reference: string | null;
  cover_reference: string | null;
  location: string | null;
  timezone: string | null;
  verified: boolean;
  follower_count: number;
  languages: { id: string; code: string; label: string }[];
  categories: { id: string; code: string; label: string }[];
  social_links: { label: string; url: string }[];
};

export type PublicSubscriptionOption = {
  duration: string;
  base_amount_minor: number;
  effective_amount_minor: number;
  currency: string;
  discount_basis_points: number;
};

export type MarketplaceListingMedia = {
  derivative_id: string;
  delivery_path: string;
  position: number;
  width: number | null;
  height: number | null;
};

export type MarketplaceListing = {
  id: string;
  public_id: string;
  owner_creator_id: string;
  title: string;
  description: string | null;
  category: string;
  condition: string;
  status: string;
  quantity_available: number;
  price_amount_minor: number;
  currency: string;
  shipping_mode: string;
  origin_country_code: string;
  shipping_charged_minor: number;
  media?: MarketplaceListingMedia[];
  seller?: {
    creator_id: string;
    username: string;
    display_name: string;
    avatar_reference: string | null;
    verified: boolean;
  } | null;
};

export const PUBLIC_ENTITY_TYPES: readonly DiscoveryEntityType[] = [
  "creator",
  "post",
  "video",
  "gallery",
  "marketplace_listing",
  "live_room",
];

export function discoverySearchPath({
  query,
  types,
  cursor,
  category,
  liveNow,
  sort,
  limit = 50,
}: {
  query?: string;
  types?: readonly DiscoveryEntityType[];
  cursor?: string | null;
  category?: string;
  liveNow?: boolean;
  sort?: "relevance" | "trending" | "newest" | "price_asc" | "price_desc" | "live";
  limit?: number;
} = {}): string {
  const params = new URLSearchParams();
  const trimmed = query?.trim();
  if (trimmed) params.set("q", trimmed);
  types?.forEach((type) => params.append("types", type));
  if (cursor) params.set("cursor", cursor);
  if (category) params.set("category", category);
  if (liveNow) params.set("live_now", "true");
  if (sort) params.set("sort", sort);
  params.set("limit", String(limit));
  return `/discovery/search?${params.toString()}`;
}

export function discoverPath(cursor?: string | null, limit = 50): string {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return `/discovery/discover?${params.toString()}`;
}

export function previewUrl(previewAssetId: string | null | undefined): string | undefined {
  if (!previewAssetId) return undefined;
  const base = process.env.NEXT_PUBLIC_FANBACKSTAGE_API_URL ?? "http://localhost:8000";
  return `${base}/api/v1/media/previews/${encodeURIComponent(previewAssetId)}`;
}

export function formatMoney(amountMinor: number, currency: string): string {
  const code = currency.toUpperCase();
  const formatter = new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: code,
    currencyDisplay: "symbol",
  });
  const digits = formatter.resolvedOptions().maximumFractionDigits ?? 2;
  if (digits === 0) return formatter.format(amountMinor);
  const factor = 10 ** digits;
  const major = Math.trunc(Math.abs(amountMinor) / factor);
  const fraction = Math.abs(amountMinor) % factor;
  const parts = formatter.formatToParts(major);
  const formatted = parts.map((part) => part.type === "fraction" ? String(fraction).padStart(digits, "0") : part.value).join("");
  const minus = formatter.formatToParts(-1).find((part) => part.type === "minusSign")?.value ?? "-";
  return amountMinor < 0 ? `${minus}${formatted}` : formatted;
}

export function formatDurationSeconds(seconds: number | null | undefined): string | null {
  if (seconds == null || seconds < 0 || !Number.isFinite(seconds)) return null;
  const rounded = Math.floor(seconds);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remainder = rounded % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

export function accessLabel(policy: string | null | undefined, _locked = false): string {
  if (policy === "ppv") return "PREMIUM / PPV";
  if (policy === "subscription" || policy === "subscribers") return "SUBSCRIBERS";
  if (policy === "followers") return "FOLLOWERS";
  if (policy === "private") return "PRIVATE";
  return "FREE";
}

export function creatorUsernameFor(item: DiscoveryResult): string | undefined {
  return item.creator_username ??
    (item.entity_type === "creator" ? item.subtitle?.replace(/^@/, "") || undefined : undefined);
}

export function groupDiscovery(items: readonly DiscoveryResult[]) {
  return {
    creators: items.filter((item) => item.entity_type === "creator"),
    live: items.filter((item) => item.live),
    videos: items.filter((item) => item.entity_type === "video"),
    stories: items.filter((item) => item.entity_type === "creator"),
    content: items.filter((item) => ["post", "video", "gallery"].includes(item.entity_type)),
    marketplace: items.filter((item) => item.entity_type === "marketplace_listing"),
  };
}

export async function publicCreatorOptions(
  username: string,
): Promise<PublicSubscriptionOption[]> {
  return api<PublicSubscriptionOption[]>(`/creators/${encodeURIComponent(username)}/subscription-options`);
}
