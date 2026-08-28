import type { MetadataRoute } from "next";

const webOrigin = (process.env.NEXT_PUBLIC_FANBACKSTAGE_WEB_ORIGIN ?? "http://localhost:3000")
  .replace(/\/$/, "");

const SAFE_PUBLIC_ROUTES = [
  "",
  "/creators",
  "/discover",
  "/galleries",
  "/live",
  "/marketplace",
  "/stories",
  "/videos",
] as const;

export default function sitemap(): MetadataRoute.Sitemap {
  return SAFE_PUBLIC_ROUTES.map((path) => ({
    url: `${webOrigin}${path || "/"}`,
    changeFrequency: path === "" ? "daily" : "hourly",
    priority: path === "" ? 1 : 0.7,
  }));
}
