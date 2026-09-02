import "./styles.css";
import type { Metadata, Viewport } from "next";
import { Suspense } from "react";

import { AppHeader } from "../components/app-header";
import { AuthExperienceProvider } from "../components/auth-experience";
import { LegalAcceptanceGate } from "../components/legal-acceptance";
import { RequireAuthentication } from "../components/require-authentication";
import { SiteBanner } from "../components/site-banner";
import { SiteFooter } from "../components/site-footer";

const webOrigin = process.env.NEXT_PUBLIC_FANBACKSTAGE_WEB_ORIGIN ?? "http://localhost:3000";
const privateStaging = process.env.FANBACKSTAGE_ENVIRONMENT === "staging";

export const metadata: Metadata = {
  metadataBase: new URL(webOrigin),
  applicationName: "FanBackstage",
  title: {
    default: "FanBackstage — Get closer. Go backstage.",
    template: "%s — FanBackstage",
  },
  description: "Discover creators, premium content, live experiences, and creator-led communities.",
  openGraph: {
    type: "website",
    siteName: "FanBackstage",
    title: "FanBackstage — Get closer. Go backstage.",
    description: "Discover creators, premium content, live experiences, and creator-led communities.",
  },
  twitter: {
    card: "summary",
    title: "FanBackstage — Get closer. Go backstage.",
    description: "Discover creators, premium content, live experiences, and creator-led communities.",
  },
  robots: privateStaging
    ? { index: false, follow: false, nocache: true }
    : { index: true, follow: true },
};

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#0B1021",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html data-scroll-behavior="smooth" lang="en">
      <body>
        <a className="skip-link" href="#main-content">Skip to content</a>
        <Suspense fallback={null}><SiteBanner /></Suspense>
        <AuthExperienceProvider>
          <AppHeader />
          <RequireAuthentication>
            <LegalAcceptanceGate>
              <main id="main-content">{children}</main>
              <Suspense fallback={null}><SiteFooter /></Suspense>
            </LegalAcceptanceGate>
          </RequireAuthentication>
        </AuthExperienceProvider>
      </body>
    </html>
  );
}
