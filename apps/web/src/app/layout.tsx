import "./styles.css";
import Link from "next/link";

export const metadata = { title: "FanBackstage — Get closer. Go backstage.", description: "FanBackstage account foundation" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body><header><Link href="/" className="brand">FanBackstage</Link><nav><Link href="/login">Log in</Link><Link href="/register">Create account</Link></nav></header><main>{children}</main></body></html>; }

