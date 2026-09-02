import type { Metadata } from "next";

import { FanWelcome } from "../../components/fan-welcome";

export const metadata: Metadata = {
  title: "Welcome",
  description: "Follow public creators, build your FanBackstage feed, or begin your creator application.",
};

export default function WelcomePage() {
  return <FanWelcome />;
}
