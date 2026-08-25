import { expect } from "@playwright/test";

const base =
  process.env.E2E_MAILPIT_URL ?? `http://127.0.0.1:${process.env.E2E_MAILPIT_UI_PORT ?? "8025"}`;

type MailpitMessage = {
  ID: string;
  To: { Address: string }[];
  Subject?: string;
  Snippet: string;
};

type FullMailpitMessage = MailpitMessage & { Text?: string };

async function messageDetail(message: MailpitMessage): Promise<FullMailpitMessage> {
  return (await (await fetch(`${base}/api/v1/message/${message.ID}`)).json()) as FullMailpitMessage;
}

export async function mailpitMessage(
  email: string,
  text: string,
  timeout = 15_000,
): Promise<FullMailpitMessage> {
  const deadline = Date.now() + timeout;
  let last = "";
  while (Date.now() < deadline) {
    const body = (await (await fetch(`${base}/api/v1/messages`)).json()) as { messages: MailpitMessage[] };
    for (const candidate of body.messages.filter(message =>
      message.To.some(recipient => recipient.Address === email),
    )) {
      const message = await messageDetail(candidate);
      if ((message.Text ?? message.Snippet).includes(text)) return message;
      last = message.Text ?? message.Snippet;
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  expect(last, `Mailpit email missing for ${email}`).toContain(text);
  throw new Error("unreachable");
}

export async function mailpitContains(email: string, text: string): Promise<boolean> {
  const body = (await (await fetch(`${base}/api/v1/messages`)).json()) as { messages: MailpitMessage[] };
  for (const candidate of body.messages.filter(message =>
    message.To.some(recipient => recipient.Address === email),
  )) {
    const message = await messageDetail(candidate);
    if ((message.Text ?? message.Snippet).includes(text)) return true;
  }
  return false;
}

export async function securityLink(email: string, path: string): Promise<string> {
  const message = await mailpitMessage(email, path);
  const link = message.Text?.match(/http:\/\/[^\s]+/)?.[0];
  expect(link, `Mailpit security link missing for ${email}`).toBeTruthy();
  return link!;
}
