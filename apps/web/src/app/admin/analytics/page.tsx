import { AnalyticsDashboard } from "../../../components/analytics-dashboard";

export default function AdminAnalyticsPage() {
  return <section className="card"><p className="eyebrow">ADMIN</p><h1>Platform BI</h1><p>Ledger-derived, currency-separated financial reporting.</p><AnalyticsDashboard scope="platform" /></section>;
}
