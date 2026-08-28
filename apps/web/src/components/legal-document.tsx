import Link from "next/link";

import type { LegalBodyBlock, LegalDocument } from "../lib/legal";
import { displayLegalType } from "../lib/legal";
import styles from "./legal.module.css";

export function LegalBody({ blocks }: { blocks: LegalBodyBlock[] }) {
  return (
    <div className={styles.body}>
      {blocks.map((block, index) => {
        const key = `${block.type}-${index}`;
        if (block.type === "heading") {
          if (block.level === 2) return <h2 key={key}>{block.text}</h2>;
          if (block.level === 3) return <h3 key={key}>{block.text}</h3>;
          return <h4 key={key}>{block.text}</h4>;
        }
        if (block.type === "paragraph") return <p key={key}>{block.text}</p>;
        if (block.type === "callout") {
          return <aside className={styles.callout} key={key}>{block.text}</aside>;
        }
        if (block.type === "link") {
          const internal = block.href.startsWith("/");
          return internal
            ? <Link className={styles.legalLink} href={block.href} key={key}>{block.text}</Link>
            : <a className={styles.legalLink} href={block.href} key={key} rel="noreferrer">{block.text}</a>;
        }
        const items = block.items.map((item, itemIndex) => (
          <li key={`${key}-${itemIndex}`}>{item}</li>
        ));
        return block.ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>;
      })}
    </div>
  );
}

export function LegalDocumentView({ document }: { document: LegalDocument }) {
  const effective = document.effective_from
    ? new Intl.DateTimeFormat("en", { dateStyle: "long" }).format(new Date(document.effective_from))
    : null;
  return (
    <article className={styles.documentShell}>
      <header className={styles.documentHeader}>
        <p className="eyebrow">{displayLegalType(document.document_type)}</p>
        <h1>{document.title}</h1>
        <p className={styles.meta}>
          Version {document.version}{effective ? ` · Effective ${effective}` : ""}
          {document.jurisdiction_code ? ` · ${document.jurisdiction_code}` : " · Global"}
        </p>
      </header>
      <LegalBody blocks={document.body} />
    </article>
  );
}
