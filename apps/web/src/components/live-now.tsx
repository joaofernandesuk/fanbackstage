"use client";

import Image from "next/image";
import Link from "next/link";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";

import { api, ApiError } from "../lib/api";
import {
  ComplianceAccess,
  complianceAccessFromError,
} from "../lib/compliance-api";
import { mediaForUsername } from "../lib/demo-personas";
import { completePaymentCheckout } from "../lib/payments";
import {
  creatorUsernameFor,
  discoverySearchPath,
  formatMoney,
  type DiscoveryPage,
  type DiscoveryResult,
} from "../lib/public-api";
import { CreatorAvatar, EmptyState, Skeleton, useLoginGate } from "./consumer-ui";
import { AdultAccessGate } from "./adult-access-gate";
import styles from "./social-surface.module.css";

type RoomSummary = ComplianceAccess & {
  id: string;
  public_id: string;
  creator_id: string;
  title: string;
  description: string | null;
  status: string;
  access_mode: string;
  viewer_count: number;
  started_at: string | null;
};
type Chat = { id: string; body: string; sender_user_id: string | null };
type PostedChat = Pick<Chat, "id" | "body">;
type Token = { room_id: string; provider_url: string; token: string };
type PrivateRequest = {
  id: string;
  status: string;
  per_minute_price_minor: number;
  minimum_charge_minor: number;
  currency: string;
};
type LiveActivity = {
  id: string;
  event_type: string;
  amount_minor: number | null;
  currency: string | null;
  metadata: Record<string, string>;
};
type LiveGoal = {
  id: string;
  title: string;
  target_amount_minor: number;
  progress_amount_minor: number;
  currency: string;
};
type Supporter = {
  rank: number;
  amount_minor: number;
  currency: string;
  supporter_label: string;
  viewer_is_current_user: boolean;
};
type PaidRequestOption = {
  id: string;
  label: string;
  amount_minor: number;
  currency: string;
};
type LiveCommerce = {
  id: string;
  status: string;
  payment_attempt_id: string;
};

const LIVE_REACTIONS = [
  { type: "love", symbol: "♥", label: "Love" },
  { type: "fire", symbol: "🔥", label: "Fire" },
  { type: "applause", symbol: "👏", label: "Applause" },
  { type: "wow", symbol: "✨", label: "Wow" },
] as const;

function identityFor(room: RoomSummary, creators: DiscoveryResult[]) {
  const match = creators.find((item) => item.id === room.creator_id || item.creator_id === room.creator_id);
  return {
    displayName: match?.title || room.title,
    username: match ? creatorUsernameFor(match) : undefined,
  };
}

export function LiveNow() {
  const { requireLogin } = useLoginGate();
  const [rooms, setRooms] = useState<RoomSummary[]>([]);
  const [creators, setCreators] = useState<DiscoveryResult[]>([]);
  const [active, setActive] = useState<RoomSummary | null>(null);
  const [chat, setChat] = useState<Chat[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [blockedAccess, setBlockedAccess] = useState<ComplianceAccess | null>(null);
  const [privateRequest, setPrivateRequest] = useState<PrivateRequest | null>(null);
  const [privateRequesting, setPrivateRequesting] = useState(false);
  const [activity, setActivity] = useState<LiveActivity[]>([]);
  const [goals, setGoals] = useState<LiveGoal[]>([]);
  const [supporters, setSupporters] = useState<Supporter[]>([]);
  const [reactionCounts, setReactionCounts] = useState<Record<string, number>>({});
  const [paidRequestOptions, setPaidRequestOptions] = useState<PaidRequestOption[]>([]);
  const [commerceMessage, setCommerceMessage] = useState("");
  const roomRef = useRef<Room | null>(null);
  const videoRef = useRef<HTMLDivElement | null>(null);
  const attachedTracksRef = useRef<WeakSet<Track>>(new WeakSet());

  async function refresh() {
    try {
      const [nextRooms, directory] = await Promise.all([
        api<RoomSummary[]>("/live/rooms"),
        api<DiscoveryPage>(discoverySearchPath({ types: ["creator"], sort: "trending", limit: 12 })),
      ]);
      setRooms(nextRooms.filter((room) => room.status === "live"));
      setCreators(directory.items.filter((item) => item.entity_type === "creator"));
      setError("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to load the live directory");
    } finally {
      setLoading(false);
    }
  }

  function disconnect() {
    roomRef.current?.disconnect();
    roomRef.current = null;
    attachedTracksRef.current = new WeakSet();
    videoRef.current?.replaceChildren();
    setActive(null);
    setPrivateRequest(null);
    setActivity([]);
    setGoals([]);
    setSupporters([]);
    setReactionCounts({});
    setPaidRequestOptions([]);
    setCommerceMessage("");
  }

  async function recoverLiveState(roomId: string) {
    const [messages, events, currentGoals, ranking, reactions, requestOptions] = await Promise.all([
      api<Chat[]>(`/live/rooms/${roomId}/chat`),
      api<LiveActivity[]>(`/live/rooms/${roomId}/activity`),
      api<LiveGoal[]>(`/live/rooms/${roomId}/goals`),
      api<Supporter[]>(`/live/rooms/${roomId}/supporters`),
      api<{ counts: Record<string, number> }>(`/live/rooms/${roomId}/reactions`),
      api<PaidRequestOption[]>(`/live/rooms/${roomId}/paid-request-options`),
    ]);
    setChat(messages);
    setActivity(events);
    setGoals(currentGoals);
    setSupporters(ranking);
    setReactionCounts(reactions.counts);
    setPaidRequestOptions(requestOptions);
  }

  async function join(room: RoomSummary) {
    if (!requireLogin()) return;
    if (!room.compliance_allowed) {
      setBlockedAccess(room);
      return;
    }
    try {
      disconnect();
      setError("");
      const authorization = await api<Token>(`/live/rooms/${room.id}/token`, { method: "POST" });
      const livekitRoom = new Room();
      const attachVideo = (track: Track) => {
        if (track.kind !== Track.Kind.Video || !videoRef.current || attachedTracksRef.current.has(track)) return;
        attachedTracksRef.current.add(track);
        videoRef.current.append(track.attach());
      };
      livekitRoom.on(RoomEvent.TrackSubscribed, attachVideo);
      livekitRoom.on(RoomEvent.TrackUnsubscribed, (track) => {
        attachedTracksRef.current.delete(track);
        track.detach().forEach((element) => element.remove());
      });
      await livekitRoom.connect(authorization.provider_url, authorization.token, { autoSubscribe: true });
      roomRef.current = livekitRoom;
      setActive(room);
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      for (const participant of livekitRoom.remoteParticipants.values()) {
        for (const publication of participant.trackPublications.values()) {
          if (publication.track) attachVideo(publication.track);
        }
      }
      await recoverLiveState(room.id);
    } catch (caught) {
      disconnect();
      if (caught instanceof ApiError && caught.code) {
        setBlockedAccess(complianceAccessFromError(caught));
      }
      setError(caught instanceof ApiError ? caught.message : "Unable to connect to this live room");
    }
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active) return;
    const chatForm = event.currentTarget;
    const form = new FormData(chatForm);
    try {
      const posted = await api<PostedChat>(`/live/rooms/${active.id}/chat`, {
        method: "POST",
        body: JSON.stringify({ body: form.get("body") }),
      });
      chatForm.reset();
      setChat((current) => current.some((item) => item.id === posted.id)
        ? current
        : [...current, { ...posted, sender_user_id: null }]);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to send chat");
    }
  }

  async function requestPrivateSession() {
    if (!active || !requireLogin() || privateRequesting) return;
    setPrivateRequesting(true);
    setError("");
    try {
      const request = await api<PrivateRequest>(`/live/creators/${active.creator_id}/private-requests`, {
        method: "POST",
        body: JSON.stringify({ mode: "one_to_one" }),
      });
      setPrivateRequest(request);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to request a private session");
    } finally {
      setPrivateRequesting(false);
    }
  }

  async function react(reactionType: string) {
    if (!active) return;
    try {
      const result = await api<{ counts: Record<string, number> }>(`/live/rooms/${active.id}/reactions`, {
        method: "POST",
        body: JSON.stringify({ reaction_type: reactionType }),
      });
      setReactionCounts(result.counts);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to react right now");
    }
  }

  async function submitPaidRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    setCommerceMessage("");
    try {
      const charge = await api<LiveCommerce>(`/live/rooms/${active.id}/paid-requests`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          option_id: values.get("paid-request-option"),
          message: values.get("paid-request-message"),
        }),
      });
      await completePaymentCheckout(charge.payment_attempt_id);
      form.reset();
      setCommerceMessage("Payment confirmed. Your request is waiting for the creator.");
      await recoverLiveState(active.id);
    } catch (caught) {
      setCommerceMessage(caught instanceof ApiError ? caught.message : "Unable to send paid request");
    }
  }

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => {
      window.clearInterval(timer);
      disconnect();
    };
  }, []);

  useEffect(() => {
    const livekitRoom = roomRef.current;
    if (!active || !livekitRoom || !videoRef.current) return;
    for (const participant of livekitRoom.remoteParticipants.values()) {
      for (const publication of participant.trackPublications.values()) {
        const track = publication.track;
        if (track?.kind === Track.Kind.Video && !attachedTracksRef.current.has(track)) {
          attachedTracksRef.current.add(track);
          videoRef.current.append(track.attach());
        }
      }
    }
  }, [active]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const recover = async () => {
      try {
        if (!cancelled) await recoverLiveState(active.id);
      } catch {
        // Durable Live projections are retried together. A transient API
        // failure must not disconnect otherwise healthy LiveKit media.
      }
    };
    const interval = window.setInterval(() => void recover(), 3_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [active]);

  const roomCreator = useMemo(() => active ? identityFor(active, creators) : null, [active, creators]);

  if (loading) {
    return <div className={styles.liveGrid}><Skeleton lines={1} /><Skeleton lines={1} /><Skeleton lines={1} /><Skeleton lines={1} /></div>;
  }

  return (
    <section aria-label="Live creators" className={styles.liveDirectory}>
      {error && <p className={styles.inlineMessage} role="status">{error}</p>}
      {blockedAccess && (
        <AdultAccessGate
          access={blockedAccess}
          adultRestricted={true}
          feature="live"
          onGranted={async () => {
            await refresh();
            setBlockedAccess(null);
          }}
          title="this live room"
        />
      )}

      {active && roomCreator && (
        <section aria-label={`Watching ${active.title}`} className={styles.liveViewer}>
          <h2 className="sr-only">Watching: {active.title}</h2>
          <div aria-label="Live video" className={styles.liveStage} ref={videoRef}>
            <div className={styles.liveStagePlaceholder}><span aria-hidden="true">▶</span><p>Connecting to the creator&apos;s protected live stream…</p></div>
            <div className={styles.liveStageHeader}>
              <CreatorAvatar displayName={roomCreator.displayName} live size={42} username={roomCreator.username} />
              <strong>{roomCreator.displayName}</strong>
              <span className={styles.liveBadge}>LIVE</span>
            </div>
            <button className={styles.leaveLive} onClick={disconnect} type="button">Leave live</button>
          </div>
          <aside className={styles.liveChat}>
            <h2>Live chat · {active.viewer_count} watching</h2>
            <section aria-label="Live interactions" className={styles.liveInteractions}>
              <div>
                <span>INTERACT</span>
                <strong>Make this live personal</strong>
                <p>Chat with {roomCreator.displayName} now, or queue a private 1:1 session for after this public room ends.</p>
              </div>
              {privateRequest ? (
                <p className={styles.liveInteractionSuccess} role="status">
                  Private 1:1 request queued. {roomCreator.displayName} will need to accept it before payment authorisation.
                </p>
              ) : (
                <button className={styles.privateRequestButton} disabled={privateRequesting} onClick={() => void requestPrivateSession()} type="button">
                  {privateRequesting ? "Requesting…" : "Request private 1:1"}
                </button>
              )}
              <p className={styles.liveInteractionPrice}>
                {privateRequest
                  ? `Minimum ${formatMoney(privateRequest.minimum_charge_minor, privateRequest.currency)} · ${formatMoney(privateRequest.per_minute_price_minor, privateRequest.currency)}/minute`
                  : "The creator confirms availability before any payment is authorised."}
              </p>
              <div aria-label="Live reactions">
                {LIVE_REACTIONS.map((reaction) => (
                  <button
                    aria-label={`React ${reaction.label}`}
                    key={reaction.type}
                    onClick={() => void react(reaction.type)}
                    type="button"
                  >
                    {reaction.symbol} {reactionCounts[reaction.type] ?? 0}
                  </button>
                ))}
              </div>
              {paidRequestOptions.length > 0 && (
                <form aria-label="Send a paid request" onSubmit={submitPaidRequest}>
                  <label>
                    Paid request
                    <select name="paid-request-option" required>
                      {paidRequestOptions.map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.label} · {formatMoney(option.amount_minor, option.currency)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Request details
                    <input maxLength={500} name="paid-request-message" required />
                  </label>
                  <button type="submit">Pay and send request</button>
                </form>
              )}
              {commerceMessage && <p role="status">{commerceMessage}</p>}
              {goals.map((goal) => (
                <section aria-label={`Goal: ${goal.title}`} key={goal.id}>
                  <strong>{goal.title}</strong>
                  <p>
                    {formatMoney(goal.progress_amount_minor, goal.currency)} of {formatMoney(goal.target_amount_minor, goal.currency)}
                  </p>
                  <progress max={goal.target_amount_minor} value={Math.min(goal.progress_amount_minor, goal.target_amount_minor)} />
                </section>
              ))}
              {supporters.length > 0 && (
                <section aria-label="Top supporters">
                  <strong>Top supporters this Live</strong>
                  <ol>
                    {supporters.map((supporter) => (
                      <li key={`${supporter.rank}-${supporter.supporter_label}`}>
                        {supporter.supporter_label} · {formatMoney(supporter.amount_minor, supporter.currency)}
                      </li>
                    ))}
                  </ol>
                </section>
              )}
              {activity.length > 0 && (
                <section aria-label="Live activity">
                  <strong>Live activity</strong>
                  <ul>
                    {activity.slice(-8).map((item) => (
                      <li key={item.id}>
                        {item.event_type.replaceAll("_", " ")}
                        {item.amount_minor && item.currency ? ` · ${formatMoney(item.amount_minor, item.currency)}` : ""}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {roomCreator.username && <Link className={styles.liveProfileLink} href={`/creator/${roomCreator.username}`}>View creator profile</Link>}
            </section>
            <div className={styles.liveChatMessages}>
              {chat.length ? chat.map((message) => <p key={message.id}>{message.body}</p>) : <p>Be the first to say hello.</p>}
            </div>
            <form onSubmit={send}>
              <label className="sr-only" htmlFor="live-chat-body">Live chat message</label>
              <input id="live-chat-body" maxLength={1000} name="body" placeholder="Say something…" required />
              <button type="submit">Send</button>
            </form>
          </aside>
        </section>
      )}

      {rooms.length > 0 ? (
        <div className={styles.liveGrid}>
          {rooms.map((room) => {
            const identity = identityFor(room, creators);
            const image = mediaForUsername(identity.username)?.portrait || "/images/fanbackstage-hero.png";
            return (
              <article key={room.id}>
                <button aria-label="Watch live" className={styles.liveCard} onClick={() => void join(room)} type="button">
                  <span className={styles.liveCardImage}>
                    <Image alt={`${identity.displayName} live preview`} fill sizes="(max-width: 640px) 50vw, 25vw" src={image} />
                    <span className={styles.liveBadge}>LIVE</span>
                    <span className={styles.viewerCount}>{room.viewer_count} watching</span>
                  </span>
                  <span className={styles.liveCardInfo}><strong>{room.title}</strong><span>@{identity.username || "creator"} · {room.access_mode}</span></span>
                </button>
              </article>
            );
          })}
        </div>
      ) : creators.length ? (
        <>
          <div className={styles.liveQuietNotice} role="status">
            <div><strong>No one is broadcasting right now</strong><span>The stage is between shows. Follow a creator below to catch their next real broadcast.</span></div>
            <Link className={styles.secondaryLink} href="/creators">Explore all creators</Link>
          </div>
          <div className={styles.liveGrid}>
            {creators.slice(0, 8).map((creator) => {
              const username = creatorUsernameFor(creator);
              const image = mediaForUsername(username)?.portrait || "/images/fanbackstage-hero.png";
              return (
                <Link className={styles.liveCard} href={username ? `/creator/${username}` : "/creators"} key={creator.id}>
                  <span className={styles.liveCardImage}><Image alt={`${creator.title} creator preview`} fill sizes="(max-width: 640px) 50vw, 25vw" src={image} /></span>
                  <span className={styles.liveCardInfo}><strong>{creator.title}</strong><span>{username ? `@${username}` : "Creator profile"} · Follow for live alerts</span></span>
                </Link>
              );
            })}
          </div>
        </>
      ) : (
        <EmptyState action={<Link className={styles.primaryLink} href="/discover">Open Discover</Link>} body="The live directory is temporarily quiet." title="No live creators to show" />
      )}
    </section>
  );
}
