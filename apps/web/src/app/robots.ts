import type { MetadataRoute } from "next";

const webOrigin = process.env.NEXT_PUBLIC_FANBACKSTAGE_WEB_ORIGIN ?? "http://localhost:3000";
export default function robots(): MetadataRoute.Robots {
  if (process.env.FANBACKSTAGE_ENVIRONMENT === "staging") {
    return { rules: { userAgent: "*", disallow: "/" } };
  }
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: [
        "/account/",
        "/admin/",
        "/appeals/",
        "/creator-studio/",
        "/featuring/",
        "/login/",
        "/messages/",
        "/notifications/",
        "/purchases/",
        "/register/",
        "/verify-email/",
      ],
    },
    sitemap: `${webOrigin.replace(/\/$/, "")}/sitemap.xml`,
  };
}
