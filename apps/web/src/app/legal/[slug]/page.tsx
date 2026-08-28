import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { LegalDocumentView } from "../../../components/legal-document";
import { serverLegalDocument } from "../../../lib/legal-server";

type LegalPageProps = {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ jurisdiction_code?: string; language?: string }>;
};

function safeScope(values: { jurisdiction_code?: string; language?: string }) {
  const jurisdiction = /^[A-Za-z]{2}$/.test(values.jurisdiction_code ?? "")
    ? values.jurisdiction_code!.toUpperCase()
    : "";
  const language = /^[a-z]{2}(?:-[A-Z]{2})?$/.test(values.language ?? "")
    ? values.language!
    : "en";
  return { jurisdiction, language };
}

export async function generateMetadata({ params, searchParams }: LegalPageProps): Promise<Metadata> {
  const [{ slug }, query] = await Promise.all([params, searchParams]);
  const scope = safeScope(query);
  const document = await serverLegalDocument(slug, scope.jurisdiction, scope.language);
  if (!document) return { title: "Legal document" };
  return {
    title: document.title,
    description: `${document.title}, version ${document.version}, published by FanBackstage.`,
    alternates: { canonical: `/legal/${encodeURIComponent(document.slug)}` },
    robots: { index: true, follow: true },
  };
}

export default async function LegalPage({ params, searchParams }: LegalPageProps) {
  const [{ slug }, query] = await Promise.all([params, searchParams]);
  const scope = safeScope(query);
  const document = await serverLegalDocument(slug, scope.jurisdiction, scope.language);
  if (!document) notFound();
  return <LegalDocumentView document={document} />;
}
