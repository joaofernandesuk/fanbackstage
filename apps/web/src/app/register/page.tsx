import type { Metadata } from "next";

import { AuthForm } from "../../components/auth-form";

export const metadata: Metadata = {
  title: "Join",
  description: "Create a free FanBackstage account.",
};

export default function RegisterPage() { return <AuthForm mode="register" />; }
