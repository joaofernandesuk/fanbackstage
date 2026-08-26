import { describe, expect, it } from "vitest";

import {
  accountNavigation,
  mobileNavigation,
  primaryNavigation,
  type NavigationIdentity,
} from "./navigation-model";

function identity(roles: string[], creatorUsername?: string): NavigationIdentity {
  return { email: "person@example.com", roles, creatorUsername };
}

function labels(items: readonly { label: string }[]) {
  return items.map((item) => item.label);
}

describe("consumer navigation model", () => {
  it("shows discovery-only navigation and account actions to the public", () => {
    expect(labels(primaryNavigation(null))).toEqual([
      "Home",
      "Creators",
      "Live",
      "Videos",
      "Stories",
      "Marketplace",
      "Discover",
    ]);
    expect(labels(primaryNavigation(null))).not.toEqual(
      expect.arrayContaining(["Following", "Messages", "Notifications", "Wallet"]),
    );
  });

  it("gives a fan the social shell without operational destinations", () => {
    const fan = identity([]);
    expect(labels(primaryNavigation(fan))).toEqual([
      "Home",
      "Discover",
      "Creators",
      "Live",
      "Videos",
      "Marketplace",
    ]);
    expect(labels(accountNavigation(fan))).toEqual([
      "My Profile",
      "Purchases",
      "Subscriptions",
      "Notifications",
      "Settings",
    ]);
    expect(labels(mobileNavigation(fan))).toEqual(["Home", "Discover", "Live", "Messages", "Profile"]);
  });

  it("adds creator destinations and a mobile create action for creators", () => {
    const creator = identity(["creator"], "luna-sparks");
    expect(accountNavigation(creator)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: "My Profile", href: "/creator/luna-sparks" }),
        expect.objectContaining({ label: "Creator Studio" }),
        expect.objectContaining({ label: "Analytics" }),
      ]),
    );
    expect(labels(mobileNavigation(creator))).toContain("Create");
  });

  it("adds agency destinations for managers", () => {
    const manager = identity(["manager"]);
    expect(labels(accountNavigation(manager))).toEqual(
      expect.arrayContaining(["Group / Agency", "Group Analytics", "Group Featuring"]),
    );
    expect(labels(primaryNavigation(manager))).not.toContain("Group / Agency");
  });

  it("keeps moderator operations in the account menu", () => {
    const moderator = identity(["moderator"]);
    expect(labels(accountNavigation(moderator))).toEqual(
      expect.arrayContaining(["Moderation", "Appeals", "Consent Review"]),
    );
    expect(labels(primaryNavigation(moderator))).not.toContain("Moderation");
  });

  it("adds scoped platform destinations for administrators", () => {
    const admin = identity(["admin"]);
    expect(labels(accountNavigation(admin))).toEqual(
      expect.arrayContaining([
        "Moderation",
        "Platform Analytics",
        "Discovery Controls",
        "Featuring Admin",
        "Referral Admin",
      ]),
    );
    expect(labels(primaryNavigation(admin))).not.toContain("Platform Analytics");
  });
});
