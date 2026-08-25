import "./styles.css";
import Link from "next/link";

export const metadata = { title: "FanBackstage — Get closer. Go backstage.", description: "A premium creator and community platform" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body><header><Link href="/" className="brand" aria-label="FanBackstage home">FanBackstage</Link><nav aria-label="Primary navigation"><Link href="/discover">Discover</Link><Link href="/feed">Following</Link><Link href="/live">Live</Link><Link href="/messages">Messages</Link><Link href="/notifications">Notifications</Link><Link href="/login">Log in</Link><Link className="button" href="/register">Join</Link></nav></header><main>{children}</main></body></html>; }
