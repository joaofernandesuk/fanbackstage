import { TokenForm } from "../../components/security-forms";
import { Suspense } from "react";
export default function ResetPassword() { return <Suspense fallback={<p>Loading…</p>}><TokenForm kind="reset-password" /></Suspense>; }
