export type NavigationIcon =
  | "home"
  | "discover"
  | "creators"
  | "live"
  | "videos"
  | "marketplace"
  | "messages"
  | "profile"
  | "create";

export type NavigationItem = {
  label: string;
  href: string;
  exact?: boolean;
  icon?: NavigationIcon;
  group?: "account" | "creator" | "manager" | "operations";
};

export type NavigationIdentity = {
  email: string;
  roles: string[];
  creatorUsername?: string | null;
};

export const publicNavigation: readonly NavigationItem[] = [
  { label: "Home", href: "/", exact: true, icon: "home" },
  { label: "Creators", href: "/creators", icon: "creators" },
  { label: "Live", href: "/live", icon: "live" },
  { label: "Videos", href: "/videos", icon: "videos" },
  { label: "Galleries", href: "/galleries", icon: "discover" },
  { label: "Stories", href: "/stories", icon: "discover" },
  { label: "Marketplace", href: "/marketplace", icon: "marketplace" },
  { label: "Discover", href: "/discover", icon: "discover" },
];

export const authenticatedNavigation: readonly NavigationItem[] = [
  { label: "Home", href: "/feed", icon: "home" },
  { label: "Discover", href: "/discover", icon: "discover" },
  { label: "Creators", href: "/creators", icon: "creators" },
  { label: "Live", href: "/live", icon: "live" },
  { label: "Videos", href: "/videos", icon: "videos" },
  { label: "Galleries", href: "/galleries", icon: "discover" },
  { label: "Marketplace", href: "/marketplace", icon: "marketplace" },
];

function hasAnyRole(identity: NavigationIdentity, roles: readonly string[]) {
  return roles.some((role) => identity.roles.includes(role));
}

export function primaryNavigation(identity: NavigationIdentity | null): readonly NavigationItem[] {
  return identity ? authenticatedNavigation : publicNavigation;
}

export function accountNavigation(identity: NavigationIdentity): NavigationItem[] {
  const creatorProfile = identity.creatorUsername
    ? `/creator/${identity.creatorUsername}`
    : "/creator-onboarding";
  const items: NavigationItem[] = [
    { label: "My Profile", href: hasAnyRole(identity, ["creator"]) ? creatorProfile : "/account", group: "account" },
    { label: "Purchases", href: "/purchases", group: "account" },
    { label: "Subscriptions", href: "/subscriptions", group: "account" },
    { label: "Notifications", href: "/notifications", group: "account" },
    { label: "Settings", href: "/notification-settings", group: "account" },
  ];

  if (hasAnyRole(identity, ["creator"])) {
    items.push(
      { label: "Creator Studio", href: "/creator-studio", group: "creator" },
      { label: "Analytics", href: "/creator-studio/analytics", group: "creator" },
      { label: "Marketplace", href: "/creator-studio#marketplace", group: "creator" },
      { label: "Live Studio", href: "/creator-studio#live", group: "creator" },
    );
  }

  if (hasAnyRole(identity, ["manager"])) {
    items.push(
      { label: "Group / Agency", href: "/groups", group: "manager" },
      { label: "Group Analytics", href: "/groups/analytics", group: "manager" },
      { label: "Group Featuring", href: "/groups/featuring", group: "manager" },
    );
  }

  if (hasAnyRole(identity, ["moderator", "admin", "super_admin"])) {
    items.push(
      { label: "Moderation", href: "/moderation", group: "operations" },
      { label: "Appeals", href: "/moderation/appeals", group: "operations" },
      { label: "Consent Review", href: "/moderation/consent", group: "operations" },
    );
  }

  if (hasAnyRole(identity, ["admin", "super_admin"])) {
    items.push(
      { label: "Platform Analytics", href: "/admin/analytics", group: "operations" },
      { label: "Discovery Controls", href: "/admin/discovery", group: "operations" },
      { label: "Featuring Admin", href: "/admin/featuring", group: "operations" },
      { label: "Referral Admin", href: "/admin/referrals", group: "operations" },
    );
  }

  return items;
}

export function mobileNavigation(identity: NavigationIdentity): NavigationItem[] {
  const isCreator = hasAnyRole(identity, ["creator"]);
  const profileHref = isCreator
    ? identity.creatorUsername
      ? `/creator/${identity.creatorUsername}`
      : "/creator-onboarding"
    : "/account";

  return [
    { label: "Home", href: "/feed", icon: "home" },
    { label: "Discover", href: "/discover", icon: "discover" },
    isCreator
      ? { label: "Create", href: "/creator-studio", icon: "create" }
      : { label: "Live", href: "/live", icon: "live" },
    { label: "Messages", href: "/messages", icon: "messages" },
    { label: "Profile", href: profileHref, icon: "profile" },
  ];
}

export function isNavigationItemActive(pathname: string, item: NavigationItem) {
  if (item.exact || item.href === "/") return pathname === item.href;
  const href = item.href.split("#", 1)[0];
  return pathname === href || pathname.startsWith(`${href}/`);
}
