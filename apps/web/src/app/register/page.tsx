import type { Metadata } from "next";

import { AuthForm } from "../../components/auth-form";

export const metadata: Metadata = {
  title: "Join",
  description: "Create a free FanBackstage account.",
};

export default async function RegisterPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[] }>;
}) {
  const requested = (await searchParams).next;
  return <AuthForm mode="register" nextPath={Array.isArray(requested) ? requested[0] : requested} />;
}
