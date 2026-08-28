import { serverSiteSettings } from "../lib/legal-server";
import styles from "./legal.module.css";

export async function SiteBanner() {
  let settings: Awaited<ReturnType<typeof serverSiteSettings>> = null;
  try {
    settings = await serverSiteSettings();
  } catch {
    return null;
  }
  if (!settings?.banner_active) return null;
  const message = settings.maintenance_notice ?? settings.homepage_announcement;
  if (!message) return null;
  const levelClass = settings.banner_level === "critical"
    ? styles.bannerCritical
    : settings.banner_level === "warning"
      ? styles.bannerWarning
      : styles.bannerInfo;
  return <aside className={`${styles.banner} ${levelClass}`} role="status">{message}</aside>;
}
