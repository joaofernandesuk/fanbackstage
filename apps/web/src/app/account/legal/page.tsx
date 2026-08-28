import type { Metadata } from "next";

import { LegalAcceptanceHistory } from "../../../components/legal-acceptance";

export const metadata: Metadata = {
  title: "Legal acceptance history",
  description: "Review the legal document versions accepted on your FanBackstage account.",
};

export default function AccountLegalPage() {
  return <LegalAcceptanceHistory />;
}
