import { describe, expect, it } from "vitest";

import { FACE_EFFECTS, resolveFaceEffect } from "./story-camera-effects";

describe("Story camera effects", () => {
  it("exposes only the reviewed local effect catalogue", () => {
    expect(FACE_EFFECTS.map((effect) => effect.id)).toEqual(["none", "lip-colour", "neon-eyes", "star-mask"]);
  });

  it("falls back safely for an unknown effect", () => {
    expect(resolveFaceEffect("untrusted-overlay")).toBe("none");
  });
});
