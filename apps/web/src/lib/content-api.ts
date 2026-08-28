import { api } from "./api";
import type { ComplianceAccess } from "./compliance-api";
import type { MarketplaceListingMedia } from "./public-api";

export type ContentMedia = {
  derivative_id: string;
  media_type: "image" | "video";
  delivery_path: string;
  kind: "preview" | "teaser" | "poster" | "trailer" | "image" | "playback";
  position: number;
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
};

export type PublicContent = ComplianceAccess & {
  id: string;
  content_type: "gallery" | "video";
  title: string;
  description: string | null;
  status: string;
  access_policy: "free" | "followers" | "subscription" | "ppv" | "private";
  has_access: boolean;
  locked: boolean;
  price_amount_minor: number | null;
  price_currency: string | null;
  requires_verified_consent: boolean;
  creator_id: string | null;
  creator_username: string | null;
  creator_display_name: string | null;
  published_at: string | null;
  media_count: number;
  duration_seconds: number | null;
  preview_duration_seconds?: number | null;
  previews: ContentMedia[];
  media: ContentMedia[];
  adult_access_required?: boolean;
  adult_access_granted?: boolean;
};

export function contentDeliveryUrl(path: string): string {
  if (!/^\/media\/(?:previews|derivatives)\/[A-Za-z0-9_-]{1,128}$/.test(path)) {
    throw new Error("Invalid content media path");
  }
  const base = process.env.NEXT_PUBLIC_FANBACKSTAGE_API_URL ?? "http://localhost:8000";
  return `${base}/api/v1${path}`;
}

export function marketplaceListingMediaUrl(
  media: readonly MarketplaceListingMedia[] | undefined,
): string | undefined {
  const primary = media?.reduce<MarketplaceListingMedia | undefined>(
    (first, item) => (!first || item.position < first.position ? item : first),
    undefined,
  );
  if (!primary) return undefined;
  try {
    return contentDeliveryUrl(primary.delivery_path);
  } catch {
    return undefined;
  }
}

export function getPublicContent(contentId: string): Promise<PublicContent> {
  return api<PublicContent>(`/content/public/${encodeURIComponent(contentId)}`);
}

export function getPublicCreatorContent(username: string): Promise<PublicContent[]> {
  return api<PublicContent[]>(`/content/public/by-creator/${encodeURIComponent(username)}`);
}

export function contentDetailPath(contentId: string): string {
  return `/content/${encodeURIComponent(contentId)}`;
}
