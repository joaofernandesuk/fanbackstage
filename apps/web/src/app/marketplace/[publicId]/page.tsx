import type { Metadata } from "next";

import { MarketplaceDetail } from "../../../components/marketplace-detail";

export const metadata: Metadata = {
  title: "Marketplace item",
};

export default async function MarketplaceListingPage({ params }: { params: Promise<{ publicId: string }> }) {
  const { publicId } = await params;
  return <MarketplaceDetail publicId={publicId} />;
}
