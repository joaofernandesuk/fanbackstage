import { describe, expect, it } from "vitest";

import {
  DEMO_PERSONAS,
  DEMO_USERNAMES,
  isDemoUsername,
  mediaForUsername,
  personaForUsername,
} from "./demo-personas";

describe("demo persona presentation metadata", () => {
  it("maps every approved demo username to the exact local asset contract", () => {
    expect(DEMO_USERNAMES).toHaveLength(12);
    for (const username of DEMO_USERNAMES) {
      expect(DEMO_PERSONAS[username].username).toBe(username);
      expect(mediaForUsername(username)).toEqual({
        avatar: `/demo/creators/${username}/avatar.jpg`,
        content: `/demo/creators/${username}/content.jpg`,
        cover: `/demo/creators/${username}/cover.jpg`,
        portrait: `/demo/creators/${username}/portrait.jpg`,
      });
    }
  });

  it("does not create a presentation persona for a restricted or unknown creator", () => {
    expect(isDemoUsername("reya-restricted")).toBe(false);
    expect(personaForUsername("reya-restricted")).toBeUndefined();
    expect(mediaForUsername("someone-else")).toBeUndefined();
  });
});
