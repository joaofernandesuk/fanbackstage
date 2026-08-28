import { LegalDocumentEditor } from "../../../../components/legal-admin";

export default async function LegalDocumentAdminPage({
  params,
}: {
  params: Promise<{ documentId: string }>;
}) {
  const { documentId } = await params;
  return <LegalDocumentEditor documentId={documentId} />;
}
