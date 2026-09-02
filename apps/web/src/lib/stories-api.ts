import type { ComplianceAccess } from "./compliance-api";

export type StoryAccessPolicy = "free" | "followers" | "subscription";

export type StoryCreator = {
  id: string;
  username: string;
  display_name: string;
  avatar_reference: string | null;
  verified: boolean;
};

export type StoryMedia = {
  derivative_id: string;
  mime_type: string;
  delivery_path: string;
};

export type PublicStory = ComplianceAccess & {
  id: string;
  status: "active";
  creator: StoryCreator;
  media_type: "image" | "video";
  caption: string | null;
  alt_text: string | null;
  access_policy: StoryAccessPolicy;
  created_at: string;
  published_at: string;
  expires_at: string;
  media: StoryMedia | null;
  reaction_count: number;
  reaction_counts: Record<string, number>;
  viewer_reaction: string | null;
};

export type StoryRailPage = ComplianceAccess & {
  items: PublicStory[];
  next_cursor: string | null;
};

export type StoryCreatorGroup = {
  creator: StoryCreator;
  stories: PublicStory[];
};

export function storyRailPath({
  cursor,
  limit = 50,
  creatorUsername,
}: {
  cursor?: string | null;
  limit?: number;
  creatorUsername?: string | null;
} = {}): string {
  const boundedLimit = Math.min(50, Math.max(1, Math.trunc(limit)));
  const params = new URLSearchParams({ limit: String(boundedLimit) });
  if (cursor) params.set("cursor", cursor);
  if (creatorUsername?.trim()) params.set("creator_username", creatorUsername.trim().toLowerCase());
  return `/stories/rail?${params.toString()}`;
}

export function storyDetailPath(id: string): string {
  return `/stories/${encodeURIComponent(id)}`;
}

export function storyReportPath(id: string): string {
  return `/feed/reports/story/${encodeURIComponent(id)}`;
}

export function groupStoriesByCreator(
  stories: readonly PublicStory[],
  now = Date.now(),
): StoryCreatorGroup[] {
  const groups = new Map<string, StoryCreatorGroup>();

  for (const story of stories) {
    if (!isRenderableStory(story, now)) continue;
    const existing = groups.get(story.creator.id);
    if (existing) existing.stories.push(story);
    else groups.set(story.creator.id, { creator: story.creator, stories: [story] });
  }

  return [...groups.values()];
}

export function isRenderableStory(story: PublicStory, now = Date.now()): boolean {
  if (story.status !== "active") return false;
  const expiresAt = Date.parse(story.expires_at);
  if (!Number.isFinite(expiresAt) || expiresAt <= now) return false;
  return !story.compliance_allowed || storyMediaUrl(story) !== undefined;
}

export function storyMediaUrl(story: PublicStory): string | undefined {
  if (!story.compliance_allowed || !story.media) return undefined;
  const expectedPath = `/stories/${encodeURIComponent(story.id)}/media`;
  if (story.media.delivery_path !== expectedPath) return undefined;

  const mimeMatches = story.media_type === "video"
    ? story.media.mime_type.startsWith("video/")
    : story.media.mime_type.startsWith("image/");
  if (!mimeMatches) return undefined;

  const base = process.env.NEXT_PUBLIC_FANBACKSTAGE_API_URL ?? "http://localhost:8000";
  return `${base}/api/v1${expectedPath}`;
}

export function storyProfilePath(username: string): string {
  return `/creator/${encodeURIComponent(username)}`;
}

export function storyAgeLabel(publishedAt: string, now = Date.now()): string {
  const timestamp = Date.parse(publishedAt);
  if (!Number.isFinite(timestamp)) return "Recently";
  const seconds = Math.max(0, Math.floor((now - timestamp) / 1000));
  if (seconds < 60) return "Now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}
