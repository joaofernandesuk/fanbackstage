import { describe, expect, it } from "vitest";

import { relativeNotificationTime } from "./notification-popover";

describe("relativeNotificationTime", () => {
  const now = Date.parse("2026-08-26T12:00:00Z");

  it("formats recent notification timestamps compactly", () => {
    expect(relativeNotificationTime("2026-08-26T11:59:45Z", now)).toBe("Now");
    expect(relativeNotificationTime("2026-08-26T11:48:00Z", now)).toBe("12m");
    expect(relativeNotificationTime("2026-08-26T07:00:00Z", now)).toBe("5h");
    expect(relativeNotificationTime("2026-08-23T12:00:00Z", now)).toBe("3d");
  });
});
