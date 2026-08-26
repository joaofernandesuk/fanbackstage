import type { ComponentProps } from "react";

import type { NavigationIcon } from "./navigation-model";

type IconProps = Omit<ComponentProps<"svg">, "children">;

function Icon({ children, ...props }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      focusable="false"
      viewBox="0 0 24 24"
      {...props}
    >
      {children}
    </svg>
  );
}

export function HomeIcon(props: IconProps) {
  return <Icon {...props}><path d="m3 10.75 9-7.25 9 7.25v9a1 1 0 0 1-1 1h-5.25v-6.5h-5.5v6.5H4a1 1 0 0 1-1-1v-9Z" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" /></Icon>;
}

export function CompassIcon(props: IconProps) {
  return <Icon {...props}><circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" /><path d="m15.75 8.25-2.2 5.3-5.3 2.2 2.2-5.3 5.3-2.2Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.8" /></Icon>;
}

export function CreatorsIcon(props: IconProps) {
  return <Icon {...props}><path d="M16.5 20.25v-1.5a4 4 0 0 0-4-4h-5a4 4 0 0 0-4 4v1.5M10 10.75a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM16.75 4.1a3.5 3.5 0 0 1 0 6.3M18 14.85a4 4 0 0 1 2.5 3.7v1.7" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" /></Icon>;
}

export function LiveIcon(props: IconProps) {
  return <Icon {...props}><circle cx="12" cy="12" fill="currentColor" r="2" /><path d="M8.1 8.1a5.5 5.5 0 0 0 0 7.8M15.9 8.1a5.5 5.5 0 0 1 0 7.8M5.2 5.2a9.6 9.6 0 0 0 0 13.6M18.8 5.2a9.6 9.6 0 0 1 0 13.6" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" /></Icon>;
}

export function VideoIcon(props: IconProps) {
  return <Icon {...props}><rect height="14" rx="2" stroke="currentColor" strokeWidth="1.8" width="18" x="3" y="5" /><path d="m10 9 5 3-5 3V9Z" fill="currentColor" stroke="currentColor" strokeLinejoin="round" /></Icon>;
}

export function MarketplaceIcon(props: IconProps) {
  return <Icon {...props}><path d="M4.5 9.25v10.5h15V9.25M3 9.25l1.5-5h15l1.5 5a2.5 2.5 0 0 1-4.5 1.5 2.5 2.5 0 0 1-4.5 0 2.5 2.5 0 0 1-4.5 0A2.5 2.5 0 0 1 3 9.25Z" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" /><path d="M9 19.75v-5.5h6v5.5" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.8" /></Icon>;
}

export function SearchIcon(props: IconProps) {
  return <Icon {...props}><circle cx="10.75" cy="10.75" r="6.75" stroke="currentColor" strokeWidth="1.8" /><path d="m16 16 4 4" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" /></Icon>;
}

export function MessageIcon(props: IconProps) {
  return <Icon {...props}><path d="M20.5 11.5a8 8 0 0 1-8.4 8l-3.4 1.65.5-2.9A8 8 0 1 1 20.5 11.5Z" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" /><path d="M8.25 11.75h.01M12 11.75h.01M15.75 11.75h.01" stroke="currentColor" strokeLinecap="round" strokeWidth="2.3" /></Icon>;
}

export function BellIcon(props: IconProps) {
  return <Icon {...props}><path d="M18.25 9.5a6.25 6.25 0 0 0-12.5 0c0 7-2.5 7-2.5 7h17.5s-2.5 0-2.5-7Z" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" /><path d="M14.25 19.25a2.5 2.5 0 0 1-4.5 0" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" /></Icon>;
}

export function MenuIcon(props: IconProps) {
  return <Icon {...props}><path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeLinecap="round" strokeWidth="1.9" /></Icon>;
}

export function UserIcon(props: IconProps) {
  return <Icon {...props}><circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="1.8" /><path d="M4.5 20a7.5 7.5 0 0 1 15 0" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" /></Icon>;
}

export function PlusIcon(props: IconProps) {
  return <Icon {...props}><circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" /><path d="M12 8v8M8 12h8" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" /></Icon>;
}

export function ChevronDownIcon(props: IconProps) {
  return <Icon {...props}><path d="m7 9.5 5 5 5-5" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.9" /></Icon>;
}

export function LogOutIcon(props: IconProps) {
  return <Icon {...props}><path d="M10 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h5M14 8l4 4-4 4M9 12h9" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" /></Icon>;
}

export function ShieldIcon(props: IconProps) {
  return <Icon {...props}><path d="M12 3 5 6v5.25c0 4.1 2.85 7.8 7 9.75 4.15-1.95 7-5.65 7-9.75V6l-7-3Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.8" /><path d="m9 12 2 2 4-4" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" /></Icon>;
}

export function ReceiptIcon(props: IconProps) {
  return <Icon {...props}><path d="M6 3.5h12v17l-2.25-1.5-1.75 1.5-2-1.5-2 1.5L8.25 19 6 20.5v-17Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.8" /><path d="M9 8h6M9 12h6" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" /></Icon>;
}

export function CheckIcon(props: IconProps) {
  return <Icon {...props}><path d="m5 12.5 4.25 4.25L19 7" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" /></Icon>;
}

export function NavigationItemIcon({ icon, ...props }: IconProps & { icon?: NavigationIcon }) {
  switch (icon) {
    case "home": return <HomeIcon {...props} />;
    case "discover": return <CompassIcon {...props} />;
    case "creators": return <CreatorsIcon {...props} />;
    case "live": return <LiveIcon {...props} />;
    case "videos": return <VideoIcon {...props} />;
    case "marketplace": return <MarketplaceIcon {...props} />;
    case "messages": return <MessageIcon {...props} />;
    case "create": return <PlusIcon {...props} />;
    case "profile": return <UserIcon {...props} />;
    default: return <CompassIcon {...props} />;
  }
}

export function NotificationTypeIcon({ type, ...props }: IconProps & { type: string }) {
  const normalized = type.toLowerCase();
  if (/message|reply|conversation/.test(normalized)) return <MessageIcon {...props} />;
  if (/purchase|payment|subscription|marketplace|receipt|refund/.test(normalized)) return <ReceiptIcon {...props} />;
  if (/security|password|email|verification|account/.test(normalized)) return <ShieldIcon {...props} />;
  if (/live|stream|session/.test(normalized)) return <LiveIcon {...props} />;
  return <BellIcon {...props} />;
}
