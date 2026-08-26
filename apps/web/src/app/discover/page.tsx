import { Discovery } from "../../components/discovery";
import styles from "./page.module.css";

export default function DiscoverPage() {
  return (
    <div className={styles.page}>
      <header className={styles.intro}>
        <p>Explore FanBackstage</p>
        <h1>Discover your next favorite creator.</h1>
        <span>Public creators, safe previews, live moments and creator-owned finds—grouped for easy browsing.</span>
      </header>
      <Discovery />
    </div>
  );
}
