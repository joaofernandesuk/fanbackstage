"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Room, Track } from "livekit-client";

import { api, ApiError } from "../lib/api";
import styles from "./social-surface.module.css";

type LiveRoom = { id: string; title: string; status: string; access_mode: string };
type Token = { provider_url: string; token: string };
type Chat = { id: string; body: string; sender_user_id: string | null };
type PostedChat = Pick<Chat, "id" | "body">;
type LiveSettings = {
  private_sessions_enabled: boolean;
  one_to_one_price_minor: number;
  two_to_one_price_minor: number;
  currency: string;
  minimum_minutes: number;
  max_authorization_minor: number;
};
type PrivateRequest = {
  id: string;
  mode: string;
  per_minute_price_minor: number;
  minimum_charge_minor: number;
  currency: string;
  expires_at: string;
  invitation_status: string;
  invited_viewer_label: string | null;
};

function formatMoney(amountMinor: number, currency: string) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amountMinor / 100);
}

export function LiveStudio() {
  const [room, setRoom] = useState<LiveRoom | null>(null);
  const [settings, setSettings] = useState<LiveSettings | null>(null);
  const [message, setMessage] = useState("");
  const [roomChecked, setRoomChecked] = useState(false);
  const [publisherConnected, setPublisherConnected] = useState(false);
  const [chat, setChat] = useState<Chat[]>([]);
  const [privateRequests, setPrivateRequests] = useState<PrivateRequest[]>([]);
  const providerRoom = useRef<Room | null>(null);
  const publisherVideo = useRef<HTMLDivElement | null>(null);
  const liveActionInFlight = useRef(false);
  const [liveActionPending, setLiveActionPending] = useState(false);

  useEffect(() => {
    void api<LiveSettings>("/live/settings").then(setSettings).catch(() => undefined);
    void refreshPrivateRequests();
    void refreshCurrentRoom();
    return () => {
      providerRoom.current?.disconnect();
      publisherVideo.current?.replaceChildren();
    };
  }, []);

  // Publishing succeeds before React has necessarily committed the preview
  // container.  Mount only after that container exists; otherwise a real
  // camera track can be published while the Studio remains on its placeholder.
  useEffect(() => {
    if (!publisherConnected) return;
    try {
      mountCamera();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Unable to show your camera preview");
    }
  }, [publisherConnected, room?.id]);

  function disconnectPublisher() {
    providerRoom.current?.disconnect();
    providerRoom.current = null;
    publisherVideo.current?.replaceChildren();
    setPublisherConnected(false);
  }

  function requireCamera(livekitRoom: Room) {
    const publication = livekitRoom.localParticipant.getTrackPublication(Track.Source.Camera);
    const track = publication?.track;
    if (!track || track.kind !== Track.Kind.Video) {
      throw new Error("Camera track was not published");
    }
    return track;
  }

  function mountCamera() {
    const livekitRoom = providerRoom.current;
    const mount = publisherVideo.current;
    if (!livekitRoom || !mount) return;
    const track = requireCamera(livekitRoom);
    mount.replaceChildren();
    const preview = track.attach() as HTMLVideoElement;
    preview.autoplay = true;
    preview.muted = true;
    preview.playsInline = true;
    mount.append(preview);
  }

  async function refreshChat(activeRoom: LiveRoom) {
    setChat(await api<Chat[]>(`/live/rooms/${activeRoom.id}/chat`));
  }

  async function refreshPrivateRequests() {
    try {
      setPrivateRequests(await api<PrivateRequest[]>("/live/private-requests/mine/creator"));
    } catch {
      // A viewer does not have a creator queue; keep Studio usable while its
      // own eligibility state is being resolved.
      setPrivateRequests([]);
    }
  }


  async function resolvePrivateRequest(request: PrivateRequest, action: "accept" | "decline") {
    if (action === "accept" && request.mode === "two_to_one" && request.invitation_status !== "accepted") {
      setMessage(`Waiting for ${request.invited_viewer_label ?? "the invited fan"} to accept before this request can proceed.`);
      return;
    }
    try {
      await api(`/live/private-requests/${request.id}/${action}`, { method: "POST" });
      setPrivateRequests((current) => current.filter((item) => item.id !== request.id));
      setMessage(
        action === "accept"
          ? "Private session accepted. Payment authorization must complete before anyone can join."
          : "Private session request declined. No payment was initiated.",
      );
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to update private session request");
    }
  }

  useEffect(() => {
    if (!room || room.status !== "live" || !publisherConnected) return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const messages = await api<Chat[]>(`/live/rooms/${room.id}/chat`);
        if (!cancelled) setChat(messages);
      } catch {
        // Chat delivery is best-effort here. The next bounded refresh will retry
        // without interrupting an otherwise healthy broadcast.
      }
    };
    const interval = window.setInterval(() => void refresh(), 3_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [room, publisherConnected]);

  async function refreshCurrentRoom() {
    try {
      setRoom(await api<LiveRoom | null>("/live/rooms/mine"));
    } catch {
      // The Studio eligibility view owns user-facing setup guidance. A failed
      // recovery query must not turn a not-yet-live creator into a false live state.
      setRoom(null);
    } finally {
      setRoomChecked(true);
    }
  }

  useEffect(() => {
    if (!room || room.status !== "ending") return;
    let cancelled = false;
    let nextRefresh: number | undefined;

    const refreshEndingRoom = async () => {
      try {
        const current = await api<LiveRoom | null>("/live/rooms/mine");
        if (cancelled) return;
        setRoom(current);
        if (!current || current.status === "ended") {
          setMessage("Live room ended. You can now accept queued private requests.");
          await refreshPrivateRequests();
          return;
        }
      } catch {
        // Provider termination is durable and retryable. Keep the deny-first
        // state visible and retry this bounded status read without overlapping
        // requests or weakening room authority.
      }
      if (!cancelled) nextRefresh = window.setTimeout(() => void refreshEndingRoom(), 1_000);
    };

    void refreshEndingRoom();
    return () => {
      cancelled = true;
      if (nextRefresh !== undefined) window.clearTimeout(nextRefresh);
    };
  }, [room?.id, room?.status]);

  async function savePrivatePricing(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    try {
      const updated = await api<LiveSettings>("/live/settings", {
        method: "PATCH",
        body: JSON.stringify({
          private_sessions_enabled: values.get("private-sessions-enabled") === "on",
          one_to_one_price_minor: Number(values.get("one-to-one-price")),
          two_to_one_price_minor: Number(values.get("two-to-one-price")),
          currency: values.get("private-currency"),
          minimum_minutes: Number(values.get("private-minimum-minutes")),
          max_authorization_minor: Number(values.get("private-max-authorization")),
        }),
      });
      setSettings(updated);
      setMessage("Private-session pricing saved. It applies to future requests only.");
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to save private-session pricing");
    }
  }

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const values = new FormData(event.currentTarget);
    if (liveActionInFlight.current) return;
    liveActionInFlight.current = true;
    setLiveActionPending(true);
    try {
      const started = await api<LiveRoom>("/live/rooms", { method: "POST", body: JSON.stringify({ title: values.get("title"), access_mode: values.get("access_mode") }) });
      const authorization = await api<Token>(`/live/rooms/${started.id}/token`, { method: "POST" });
      const livekitRoom = new Room();
      await livekitRoom.connect(authorization.provider_url, authorization.token);
      await livekitRoom.localParticipant.enableCameraAndMicrophone();
      if (livekitRoom.localParticipant.trackPublications.size < 2) {
        throw new Error("Camera and microphone tracks were not published");
      }
      requireCamera(livekitRoom);
      providerRoom.current = livekitRoom;
      setRoom(started);
      setPublisherConnected(true);
      await refreshChat(started);
      setMessage("You are live. Your camera, microphone, and creator chat are ready.");
    } catch (caught) {
      disconnectPublisher();
      if (caught instanceof ApiError && caught.message === "Creator already has an active public live room") {
        await refreshCurrentRoom();
        setMessage("Your public room is already live. Rejoin it or end it before starting another.");
        return;
      }
      setMessage(caught instanceof ApiError ? caught.message : "Unable to start live room");
    } finally {
      liveActionInFlight.current = false;
      setLiveActionPending(false);
    }
  }

  async function rejoin() {
    if (!room || room.status !== "live") return;
    if (liveActionInFlight.current) return;
    liveActionInFlight.current = true;
    setLiveActionPending(true);
    try {
      disconnectPublisher();
      const authorization = await api<Token>(`/live/rooms/${room.id}/token`, { method: "POST" });
      const livekitRoom = new Room();
      await livekitRoom.connect(authorization.provider_url, authorization.token);
      await livekitRoom.localParticipant.enableCameraAndMicrophone();
      if (livekitRoom.localParticipant.trackPublications.size < 2) {
        throw new Error("Camera and microphone tracks were not published");
      }
      requireCamera(livekitRoom);
      providerRoom.current = livekitRoom;
      setPublisherConnected(true);
      await refreshChat(room);
      setMessage("You are live again. Your camera, microphone, and creator chat are ready.");
    } catch (caught) {
      disconnectPublisher();
      setMessage(caught instanceof ApiError ? caught.message : "Unable to rejoin live room");
    } finally {
      liveActionInFlight.current = false;
      setLiveActionPending(false);
    }
  }

  async function end() {
    if (!room) return;
    try { disconnectPublisher(); setRoom(await api<LiveRoom>(`/live/rooms/${room.id}/end`, { method: "POST" })); setMessage("Ending live for everyone…"); }
    catch (caught) { setMessage(caught instanceof ApiError ? caught.message : "Unable to end live room"); }
  }

  async function sendChat(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!room) return;
    const chatForm = event.currentTarget;
    const form = new FormData(chatForm);
    try {
      const posted = await api<PostedChat>(`/live/rooms/${room.id}/chat`, {
        method: "POST",
        body: JSON.stringify({ body: form.get("body") }),
      });
      chatForm.reset();
      setChat((current) => current.some((item) => item.id === posted.id)
        ? current
        : [...current, { ...posted, sender_user_id: null }]);
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to send chat");
    }
  }

  return <section className="card" aria-label="Live Studio"><p className="eyebrow">LIVE STUDIO</p><h2>Go live</h2>{!roomChecked ? <p>Checking your current live room…</p> : !room || room.status === "ended" ? <form onSubmit={start}><label>Live title<input name="title" maxLength={160} required /></label><label>Audience<select name="access_mode" defaultValue="public"><option value="public">Public</option><option value="followers">Followers</option><option value="subscribers">Subscribers</option></select></label><button disabled={liveActionPending}>{liveActionPending ? "Starting live…" : "Start live"}</button></form> : <><p className="eyebrow">{room.status === "ending" ? "ENDING LIVE" : "YOU ARE LIVE"}</p><h3>{room.title}</h3>{room.status === "ending" ? <p>Your room is closing for everyone. We will not let a new public room start until that has finished.</p> : <><p>{publisherConnected ? "Your camera and microphone are connected. This is the preview your audience receives." : "Your public room is still active. Rejoin to publish again, or end it for everyone before starting a new one."}</p>{publisherConnected && <section className={styles.liveViewer} aria-label="Your live broadcast"><div aria-label="Your live camera preview" className={styles.liveStage} ref={publisherVideo}><div className={styles.liveStagePlaceholder}><span aria-hidden="true">●</span><p>Starting your camera…</p></div><div className={styles.liveStageHeader}><strong>You are live</strong><span className={styles.liveBadge}>LIVE</span></div></div><aside className={`${styles.liveChat} ${styles.liveChatCreator}`}><h2>Creator chat</h2><div className={styles.liveChatMessages}>{chat.length ? chat.map((item) => <p key={item.id}>{item.body}</p>) : <p>Say hello to everyone watching.</p>}</div><form onSubmit={sendChat}><label className="sr-only" htmlFor="creator-live-chat">Live chat message</label><input id="creator-live-chat" maxLength={1000} name="body" placeholder="Chat with your audience…" required /><button type="submit">Send</button></form></aside></section>}{!publisherConnected && <button disabled={liveActionPending} onClick={() => void rejoin()}>{liveActionPending ? "Joining live…" : "Rejoin live"}</button>}<button disabled={liveActionPending} onClick={() => void end()}>End public live</button><p>Queued private sessions cannot be accepted until this public room has ended.</p></>}</>}{privateRequests.length > 0 && <section aria-label="Private session requests"><h2>Private session requests</h2><p>End your public live before accepting a request.</p><ul>{privateRequests.map((request) => <li key={request.id}><strong>{request.mode === "two_to_one" ? "2-to-1" : "1:1"}</strong> · {formatMoney(request.per_minute_price_minor, request.currency)}/minute · minimum {formatMoney(request.minimum_charge_minor, request.currency)}<button disabled={Boolean(room && room.status !== "ended")} onClick={() => void resolvePrivateRequest(request, "accept")} type="button">Accept</button><button onClick={() => void resolvePrivateRequest(request, "decline")} type="button">Decline</button></li>)}</ul></section>}{settings && <form aria-label="Private session pricing" onSubmit={savePrivatePricing}><h2>Private session pricing</h2><label><input name="private-sessions-enabled" type="checkbox" defaultChecked={settings.private_sessions_enabled} /> Enable private sessions</label><label>1:1 per-minute price (minor units)<input name="one-to-one-price" type="number" min="1" defaultValue={settings.one_to_one_price_minor} required /></label><label>2-to-1 per-minute price (minor units)<input name="two-to-one-price" type="number" min="1" defaultValue={settings.two_to_one_price_minor} required /></label><label>Currency<input name="private-currency" defaultValue={settings.currency} minLength={3} maxLength={3} required /></label><label>Minimum minutes<input name="private-minimum-minutes" type="number" min="1" defaultValue={settings.minimum_minutes} required /></label><label>Maximum authorization (minor units)<input name="private-max-authorization" type="number" min="1" defaultValue={settings.max_authorization_minor} required /></label><button>Save private-session pricing</button></form>}{message && <p role="status">{message}</p>}</section>;
}
