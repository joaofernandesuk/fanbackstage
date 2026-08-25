import "./styles.css";
import { AppHeader } from "../components/app-header";

export const metadata = { title: "FanBackstage — Get closer. Go backstage.", description: "A premium creator and community platform" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body><AppHeader /><main>{children}</main></body></html>; }
