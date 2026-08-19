import { TokenForm } from "../../components/security-forms";
import { Suspense } from "react";
export default function VerifyEmail() { return <Suspense fallback={<p>Loading…</p>}><TokenForm kind="verify-email" /></Suspense>; }
