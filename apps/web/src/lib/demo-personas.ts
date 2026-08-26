export const DEMO_USERNAMES = [
  "luna-sparks",
  "mira-nova",
  "ivy-ember",
  "skye-live",
  "nora-market",
  "aria-group",
  "nova-blue",
  "zara-pulse",
  "sienna-ray",
  "atlas-reed",
  "valentina-cruz",
  "sera-kim",
] as const;

export type DemoUsername = (typeof DEMO_USERNAMES)[number];

export type DemoMedia = Readonly<{
  avatar: string;
  content: string;
  cover: string;
  portrait: string;
}>;

export type DemoStorySlide = Readonly<{
  eyebrow: string;
  title: string;
  body: string;
  media: keyof Pick<DemoMedia, "content" | "cover" | "portrait">;
}>;

export type DemoPersona = Readonly<{
  username: DemoUsername;
  displayName: string;
  accent: "cyan" | "violet" | "pink" | "orange";
  editorialLabel: string;
  storySlides: readonly DemoStorySlide[];
}>;

function media(username: DemoUsername): DemoMedia {
  const root = `/demo/creators/${username}`;
  return {
    avatar: `${root}/avatar.jpg`,
    content: `${root}/content.jpg`,
    cover: `${root}/cover.jpg`,
    portrait: `${root}/portrait.jpg`,
  };
}

const PERSONAS: readonly DemoPersona[] = [
  ["luna-sparks", "Luna Sparks", "cyan", "Studio diaries"],
  ["mira-nova", "Mira Nova", "violet", "New drop"],
  ["ivy-ember", "Ivy Ember", "pink", "After hours"],
  ["skye-live", "Skye Live", "orange", "Live moments"],
  ["nora-market", "Nora Market", "cyan", "From the archive"],
  ["aria-group", "Aria Group", "violet", "Together backstage"],
  ["nova-blue", "Nova Blue", "cyan", "Blue room notes"],
  ["zara-pulse", "Zara Pulse", "pink", "On set"],
  ["sienna-ray", "Sienna Ray", "orange", "Golden hour"],
  ["atlas-reed", "Atlas Reed", "violet", "Creator process"],
  ["valentina-cruz", "Valentina Cruz", "pink", "Weekend edit"],
  ["sera-kim", "Sera Kim", "cyan", "Close friends"],
].map(([username, displayName, accent, editorialLabel]) => ({
  username: username as DemoUsername,
  displayName,
  accent: accent as DemoPersona["accent"],
  editorialLabel,
  storySlides: [
    {
      eyebrow: "Backstage now",
      title: editorialLabel,
      body: `A closer look at ${displayName}'s latest creative session.`,
      media: "portrait" as const,
    },
    {
      eyebrow: "Studio notes",
      title: "Made for this community",
      body: "A preview from the creator's public demo collection.",
      media: "content" as const,
    },
  ],
}));

export const DEMO_PERSONAS: Readonly<Record<DemoUsername, DemoPersona>> = Object.freeze(
  Object.fromEntries(PERSONAS.map((persona) => [persona.username, persona])) as Record<
    DemoUsername,
    DemoPersona
  >,
);

export const demoPersonas = PERSONAS;

export function isDemoUsername(value: string | null | undefined): value is DemoUsername {
  return typeof value === "string" && Object.hasOwn(DEMO_PERSONAS, value);
}

export function personaForUsername(
  username: string | null | undefined,
): DemoPersona | undefined {
  return isDemoUsername(username) ? DEMO_PERSONAS[username] : undefined;
}

export function mediaForUsername(username: string | null | undefined): DemoMedia | undefined {
  return isDemoUsername(username) ? media(username) : undefined;
}
