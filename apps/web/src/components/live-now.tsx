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
import {
  creatorUsernameFor,
  discoverySearchPath,
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
type Token = { room_id: string; provider_url: string; token: string };

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
      setChat(await api<Chat[]>(`/live/rooms/${room.id}/chat`));
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
    const form = new FormData(event.currentTarget);
    try {
      await api(`/live/rooms/${active.id}/chat`, {
        method: "POST",
        body: JSON.stringify({ body: form.get("body") }),
      });
      event.currentTarget.reset();
      setChat(await api<Chat[]>(`/live/rooms/${active.id}/chat`));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to send chat");
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
