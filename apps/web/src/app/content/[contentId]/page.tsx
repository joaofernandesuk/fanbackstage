import { ContentDetail } from "../../../components/content-detail";
import styles from "./page.module.css";

export default async function ContentDetailPage({
  params,
}: {
  params: Promise<{ contentId: string }>;
}) {
  const { contentId } = await params;
  return <div className={styles.page}><ContentDetail contentId={contentId} /></div>;
}
