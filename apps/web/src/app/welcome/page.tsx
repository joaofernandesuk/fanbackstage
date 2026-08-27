import type { Metadata } from "next";

import { FanWelcome } from "../../components/fan-welcome";

export const metadata: Metadata = {
  title: "Welcome",
  description: "Follow public creators and build your FanBackstage feed.",
};

export default function WelcomePage() {
  return <FanWelcome />;
}
