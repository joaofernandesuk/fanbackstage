import "./styles.css";
import type { Metadata, Viewport } from "next";

import { AppHeader } from "../components/app-header";
import { AuthExperienceProvider } from "../components/auth-experience";

export const metadata: Metadata = {
  applicationName: "FanBackstage",
  title: {
    default: "FanBackstage — Get closer. Go backstage.",
    template: "%s — FanBackstage",
  },
  description: "Discover creators, premium content, live experiences, and creator-led communities.",
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
        <AuthExperienceProvider>
          <AppHeader />
          <main id="main-content">{children}</main>
        </AuthExperienceProvider>
      </body>
    </html>
  );
}
