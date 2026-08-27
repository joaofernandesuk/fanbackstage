import type { Metadata } from "next";

import { AuthForm } from "../../components/auth-form";

export const metadata: Metadata = {
  title: "Log in",
  description: "Log in to your FanBackstage account.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[] }>;
}) {
  const requested = (await searchParams).next;
  return <AuthForm mode="login" nextPath={Array.isArray(requested) ? requested[0] : requested} />;
}
