import Link from "next/link";

import { displayLegalType, legalDocumentPath } from "../lib/legal";
import { serverLegalDocuments, serverSiteSettings } from "../lib/legal-server";
import styles from "./legal.module.css";

export async function SiteFooter() {
  let documents = [] as Awaited<ReturnType<typeof serverLegalDocuments>>;
  let settings: Awaited<ReturnType<typeof serverSiteSettings>> = null;
  try {
    [documents, settings] = await Promise.all([serverLegalDocuments(), serverSiteSettings()]);
  } catch {
    // A public shell remains usable while the API is unavailable; no draft link is fabricated.
  }
  return (
    <footer className={styles.footer}>
      <div className={styles.footerInner}>
        <nav aria-label="Legal" className={styles.footerLinks}>
          {documents.map((document) => (
            <Link href={legalDocumentPath(document.slug)} key={document.version_id}>
              {displayLegalType(document.document_type)}
            </Link>
          ))}
        </nav>
        {settings?.social_links.length ? (
          <nav aria-label="Social links" className={styles.footerSocial}>
            {settings.social_links.map((link) => (
              <a href={link.url} key={link.url} rel="noreferrer">{link.label}</a>
            ))}
          </nav>
        ) : null}
        {settings?.public_contact_text ? <p>{settings.public_contact_text}</p> : null}
        {settings?.support_email ? <a href={`mailto:${settings.support_email}`}>{settings.support_email}</a> : null}
        <p className={styles.muted}>{settings?.footer_text ?? `© ${new Date().getUTCFullYear()} FanBackstage`}</p>
      </div>
    </footer>
  );
}
