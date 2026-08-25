import { GroupAnalyticsDashboard } from "../../../components/analytics-dashboard";

export default function GroupAnalyticsPage() {
  return <section className="card"><p className="eyebrow">GROUPS</p><h1>Group analytics</h1><p>Current delegated scope and immutable historical group earnings.</p><GroupAnalyticsDashboard /></section>;
}
