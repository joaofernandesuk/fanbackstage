import type { NextConfig } from "next";

const privateStaging = process.env.FANBACKSTAGE_ENVIRONMENT === "staging";

const nextConfig: NextConfig = {
  output: "standalone",
  async headers() {
    if (!privateStaging) return [];
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Robots-Tag", value: "noindex, nofollow, noarchive" },
          { key: "Cache-Control", value: "private, no-store" },
        ],
      },
    ];
  },
};

export default nextConfig;
