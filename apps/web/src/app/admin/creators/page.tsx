import { CreatorApplications } from "../../../components/creator-applications";

export default async function CreatorApplicationsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const { status } = await searchParams;
  return <CreatorApplications initialStatus={status} />;
}
