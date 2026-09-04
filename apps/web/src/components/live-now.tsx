"use client";

import Image from "next/image";
import Link from "next/link";
import { type FormEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
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
  type PublicCreator,
  type DiscoveryPage,
  type DiscoveryResult,
} from "../lib/public-api";
import { CreatorAvatar, EmptyState, Skeleton, useLoginGate } from "./consumer-ui";
import { SubscriptionOptions } from "./subscription-options";
import { AdultAccessGate } from "./adult-access-gate";
import {
  effectForActivity,
  LIVE_REACTION_VISUALS,
  LiveStageMoments,
  type LiveStageEffect,
} from "./live-stage-moments";
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
  private_paused: boolean;
};
type Chat = { id: string; body: string; sender_user_id: string | null; sender_label: string };
type PostedChat = Chat;
type Token = { room_id: string; provider_url: string; token: string };
type PrivateRequest = {
  id: string;
  status: string;
  per_minute_price_minor: number;
  minimum_charge_minor: number;
  currency: string;
  peeks_may_be_available: boolean;
};
type PrivateInviteCandidate = { user_id: string; label: string };
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
type TipMenuItem = {
  id: string;
  label: string;
  icon: string;
  amount_minor: number;
  currency: string;
};
type GiftCatalogItem = {
  id: string;
  name: string;
  icon: string;
  amount_minor: number;
  currency: string;
  category: string | null;
};
type LiveCommerce = {
  id: string;
  status: string;
  payment_attempt_id: string;
};
type SnapshotOffer = { enabled: boolean; amount_minor: number; currency: string };
type PrivatePeekOffer = { paused: boolean; enabled: boolean; amount_minor: number | null; currency: string | null; private_session_id: string | null; viewer_admitted: boolean };
type PrivateSession = {
  id: string;
  status: string;
  mode: string;
  per_minute_price_minor: number;
  minimum_charge_minor: number;
  max_authorization_minor: number;
  currency: string;
  billable_seconds: number;
  payment_attempt_id: string | null;
  participant_role: string;
  public_live_room_id: string | null;
  peeks_allowed: boolean;
};
type LiveVipShow = {
  id: string;
  status: "preshow" | "awaiting_creator" | "active" | "completed" | "cancelled";
  title: string;
  description: string;
  goal_amount_minor: number;
  confirmed_amount_minor: number;
  buy_in_amount_minor: number;
  currency: string;
  preshow_ends_at: string;
  duration_seconds: number;
  started_at: string | null;
  ends_at: string | null;
  viewer_admitted: boolean;
};
type LiveActionPanel = "bio" | "favorite" | "subscribe" | "react" | "tip" | "gift" | "paid-request" | "snapshot" | "vip" | "private" | "settings" | "report";

function ActionIcon({ kind }: { kind: LiveActionPanel }) {
  const paths: Record<LiveActionPanel, ReactNode> = {
    react: <path d="M12 20s-8-4.8-8-10.1A4.6 4.6 0 0 1 12 6.8a4.6 4.6 0 0 1 8 3.1C20 15.2 12 20 12 20Z" />,
    bio: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></>,
    favorite: <path d="M12 20s-8-4.8-8-10.1A4.6 4.6 0 0 1 12 6.8a4.6 4.6 0 0 1 8 3.1C20 15.2 12 20 12 20Z" />,
    subscribe: <><path d="M4 6h16v12H4z"/><path d="M8 10h8M8 14h5"/></>,
    tip: <><circle cx="12" cy="12" r="8.5" /><path d="M14.8 8.5c-.7-.5-1.6-.8-2.7-.8-1.5 0-2.6.7-2.6 1.8 0 2.8 5.4 1.3 5.4 4.2 0 1.2-1.1 2-2.8 2-1.2 0-2.3-.4-3.1-1M12 5.8v12.4" /></>,
    gift: <><path d="M4 10h16v10H4zM3 7h18v3H3zM12 7v13" /><path d="M12 7H8.7C6.3 7 6 3.5 8.5 3.5 10.7 3.5 12 7 12 7Zm0 0h3.3c2.4 0 2.7-3.5.2-3.5C13.3 3.5 12 7 12 7Z" /></>,
    "paid-request": <><path d="M5 4h14v16H5zM8 8h8M8 12h5M8 16h3" /><path d="m16 14 1 1 2-2" /></>,
    private: <><rect height="11" rx="2" width="15" x="3" y="9" /><path d="M7 9V7a5 5 0 0 1 10 0v2M16 13l5-2v7l-5-2" /></>,
    snapshot: <><path d="M4 8h4l2-3h4l2 3h4v11H4z"/><circle cx="12" cy="13" r="3.5"/></>,
    vip: <><path d="m4 8 4 4 4-7 4 7 4-4-2 10H6Z"/><path d="M7 21h10"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4"/></>,
    report: <><path d="M5 21V4M5 5h12l-2 4 2 4H5" /></>,
  };
  return <svg aria-hidden="true" fill="none" viewBox="0 0 24 24"><g stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.7">{paths[kind]}</g></svg>;
}

function identityFor(room: RoomSummary, creators: DiscoveryResult[]) {
  const match = creators.find((item) => item.id === room.creator_id || item.creator_id === room.creator_id);
  return {
    displayName: match?.title || room.title,
    username: match ? creatorUsernameFor(match) : undefined,
  };
}

type LiveCardIdentity = ReturnType<typeof identityFor>;

function LiveDirectoryCard({
  identity,
  image,
  onWatch,
  room,
}: {
  identity: LiveCardIdentity;
  image: string;
  onWatch: () => void;
  room: RoomSummary;
}) {
  const previewHost = useRef<HTMLSpanElement | null>(null);
  const previewRoom = useRef<Room | null>(null);
  const previewTimer = useRef<number | null>(null);
  const hovering = useRef(false);
  const [previewing, setPreviewing] = useState(false);

  function stopPreview() {
    hovering.current = false;
    if (previewTimer.current !== null) window.clearTimeout(previewTimer.current);
    previewTimer.current = null;
    previewRoom.current?.disconnect();
    previewRoom.current = null;
    previewHost.current?.querySelectorAll("video").forEach((video) => video.remove());
    setPreviewing(false);
  }

  function schedulePreview() {
    if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    hovering.current = true;
    previewTimer.current = window.setTimeout(async () => {
      try {
        const authorization = await api<Token>(`/live/rooms/${room.id}/token`, { method: "POST" });
        if (!hovering.current) return;
        const nextRoom = new Room();
        nextRoom.on(RoomEvent.TrackSubscribed, (track) => {
          if (track.kind !== Track.Kind.Video || !previewHost.current || !hovering.current) return;
          const element = track.attach() as HTMLVideoElement;
          element.autoplay = true;
          element.muted = true;
          element.playsInline = true;
          element.className = styles.liveCardPreview;
          previewHost.current.querySelectorAll("video").forEach((video) => video.remove());
          previewHost.current.append(element);
          setPreviewing(true);
        });
        await nextRoom.connect(authorization.provider_url, authorization.token, { autoSubscribe: true });
        if (!hovering.current) {
          nextRoom.disconnect();
          return;
        }
        previewRoom.current = nextRoom;
      } catch {
        stopPreview();
      }
    }, 450);
  }

  useEffect(() => () => {
    if (previewTimer.current !== null) window.clearTimeout(previewTimer.current);
    previewRoom.current?.disconnect();
  }, []);

  return (
    <article>
      <button
        aria-label="Watch live"
        className={styles.liveCard}
        onClick={() => { stopPreview(); onWatch(); }}
        onMouseEnter={schedulePreview}
        onMouseLeave={stopPreview}
        type="button"
      >
        <span className={styles.liveCardImage} ref={previewHost}>
          <Image alt={`${identity.displayName} live preview`} fill sizes="(max-width: 640px) 50vw, 25vw" src={image} />
          <span className={styles.liveBadge}>LIVE</span>
          <span className={styles.viewerCount}>{room.viewer_count} watching</span>
          <span className={styles.livePreviewState}>{previewing ? "LIVE PREVIEW" : "HOVER TO PREVIEW"}</span>
        </span>
        <span className={styles.liveCardInfo}><strong>{room.title}</strong><span>@{identity.username || "creator"} · {room.access_mode}</span></span>
      </button>
    </article>
  );
}

export function LiveNow() {
  const { requireLogin } = useLoginGate();
  const [rooms, setRooms] = useState<RoomSummary[]>([]);
  const [creators, setCreators] = useState<DiscoveryResult[]>([]);
  const [active, setActive] = useState<RoomSummary | null>(null);
  const [liveEnded, setLiveEnded] = useState(false);
  const [chat, setChat] = useState<Chat[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [blockedAccess, setBlockedAccess] = useState<ComplianceAccess | null>(null);
  const [privateRequest, setPrivateRequest] = useState<PrivateRequest | null>(null);
  const [privateRequesting, setPrivateRequesting] = useState(false);
  const [privateInviteCandidates, setPrivateInviteCandidates] = useState<PrivateInviteCandidate[]>([]);
  const [privateInviteeId, setPrivateInviteeId] = useState("");
  const [activity, setActivity] = useState<LiveActivity[]>([]);
  const [goals, setGoals] = useState<LiveGoal[]>([]);
  const [supporters, setSupporters] = useState<Supporter[]>([]);
  const [reactionCounts, setReactionCounts] = useState<Record<string, number>>({});
  const [stageEffects, setStageEffects] = useState<LiveStageEffect[]>([]);
  const [paidRequestOptions, setPaidRequestOptions] = useState<PaidRequestOption[]>([]);
  const [tipMenu, setTipMenu] = useState<TipMenuItem[]>([]);
  const [gifts, setGifts] = useState<GiftCatalogItem[]>([]);
  const [selectedTipId, setSelectedTipId] = useState("");
  const [selectedGiftId, setSelectedGiftId] = useState("");
  const [actionPanel, setActionPanel] = useState<LiveActionPanel | null>(null);
  const [commercePending, setCommercePending] = useState(false);
  const [commerceMessage, setCommerceMessage] = useState("");
  const [hoveredTip, setHoveredTip] = useState<TipMenuItem | null>(null);
  const [hoveredTipLeft, setHoveredTipLeft] = useState<number | null>(null);
  const [snapshotOffer, setSnapshotOffer] = useState<SnapshotOffer | null>(null);
  const [vipShow, setVipShow] = useState<LiveVipShow | null>(null);
  const [peekOffer, setPeekOffer] = useState<PrivatePeekOffer | null>(null);
  const [connectedToPeek, setConnectedToPeek] = useState(false);
  const [primaryPrivateSession, setPrimaryPrivateSession] = useState<PrivateSession | null>(null);
  const [connectedToPrivate, setConnectedToPrivate] = useState(false);
  const [privateCameraEnabled, setPrivateCameraEnabled] = useState(false);
  const [vipGateRoom, setVipGateRoom] = useState<RoomSummary | null>(null);
  const [creatorProfile, setCreatorProfile] = useState<PublicCreator | null>(null);
  const [following, setFollowing] = useState(false);
  const [playbackMuted, setPlaybackMuted] = useState(false);
  const [playbackVolume, setPlaybackVolume] = useState(1);
  const [theatreMode, setTheatreMode] = useState(false);
  const [connectionState, setConnectionState] = useState<"idle" | "connecting" | "live" | "reconnecting" | "ended">("idle");
  const [railTab, setRailTab] = useState<"chat" | "activity" | "supporters">("chat");
  const [reportMessage, setReportMessage] = useState("");
  const [recoveredRoomId, setRecoveredRoomId] = useState<string | null>(null);
  const [directoryFilter, setDirectoryFilter] = useState<"all" | "public" | "followers" | "subscribers">("all");
  const [directoryQuery, setDirectoryQuery] = useState("");
  const [directorySort, setDirectorySort] = useState<"recommended" | "viewers">("recommended");
  const roomRef = useRef<Room | null>(null);
  const activeRoomRef = useRef<RoomSummary | null>(null);
  const videoRef = useRef<HTMLDivElement | null>(null);
  const attachedTracksRef = useRef<WeakSet<Track>>(new WeakSet());
  const liveRecoveryRef = useRef<Map<string, Promise<void>>>(new Map());
  const seenActivityRef = useRef<Set<string>>(new Set());
  const reactionBaselineRef = useRef<Record<string, number>>({});
  const momentsInitializedRef = useRef(false);
  const momentSequenceRef = useRef(0);
  const momentTimersRef = useRef<Set<number>>(new Set());

  function showTipTooltip(item: TipMenuItem, button: HTMLButtonElement) {
    const tray = button.closest("nav");
    if (!tray) return;
    const trayRect = tray.getBoundingClientRect();
    const buttonRect = button.getBoundingClientRect();
    const desiredCenter = buttonRect.left + buttonRect.width / 2 - trayRect.left;
    const safeEdge = Math.min(120, trayRect.width / 2);
    setHoveredTipLeft(Math.max(safeEdge, Math.min(desiredCenter, trayRect.width - safeEdge)));
    setHoveredTip(item);
  }

  function hideTipTooltip() {
    setHoveredTip(null);
    setHoveredTipLeft(null);
  }

  function showStageEffect(effect: Omit<LiveStageEffect, "id">) {
    const id = `${Date.now()}-${++momentSequenceRef.current}`;
    setStageEffects((current) => [...current.slice(-5), { ...effect, id }]);
    const timer = window.setTimeout(() => {
      setStageEffects((current) => current.filter((item) => item.id !== id));
      momentTimersRef.current.delete(timer);
    }, 4_800);
    momentTimersRef.current.add(timer);
  }

  function ingestRoomMoments(events: LiveActivity[], counts: Record<string, number>) {
    if (!momentsInitializedRef.current) {
      seenActivityRef.current = new Set(events.map((event) => event.id));
      reactionBaselineRef.current = counts;
      momentsInitializedRef.current = true;
      return;
    }
    for (const event of events) {
      if (seenActivityRef.current.has(event.id)) continue;
      seenActivityRef.current.add(event.id);
      const effect = effectForActivity(event);
      if (effect) showStageEffect(effect);
    }
    for (const reaction of LIVE_REACTION_VISUALS) {
      const previous = reactionBaselineRef.current[reaction.type] ?? 0;
      const current = counts[reaction.type] ?? 0;
      if (current > previous) {
        showStageEffect({
          kind: "reaction",
          symbol: reaction.symbol,
          title: reaction.label,
          detail: `+${current - previous} · ${current} total`,
        });
      }
    }
    reactionBaselineRef.current = counts;
  }

  async function refresh() {
    try {
      const [nextRooms, directory] = await Promise.all([
        api<RoomSummary[]>("/live/rooms"),
        api<DiscoveryPage>(discoverySearchPath({ types: ["creator"], sort: "trending", limit: 12 })),
      ]);
      setRooms(nextRooms.filter((room) => room.status === "live"));
      const watched = activeRoomRef.current;
      if (watched && !nextRooms.some((room) => room.id === watched.id && room.status === "live")) {
        roomRef.current?.disconnect();
        roomRef.current = null;
        attachedTracksRef.current = new WeakSet();
        videoRef.current?.replaceChildren();
        setLiveEnded(true);
        setConnectionState("ended");
        setActionPanel(null);
      }
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
    activeRoomRef.current = null;
    setLiveEnded(false);
    setPrivateRequest(null);
    setActivity([]);
    setGoals([]);
    setSupporters([]);
    setReactionCounts({});
    setStageEffects([]);
    setPaidRequestOptions([]);
    setTipMenu([]);
    setGifts([]);
    setActionPanel(null);
    setCommercePending(false);
    setCommerceMessage("");
    setReportMessage("");
    setHoveredTip(null);
    setSnapshotOffer(null);
    setVipShow(null);
    setPeekOffer(null);
    setConnectedToPeek(false);
    setPrimaryPrivateSession(null);
    setConnectedToPrivate(false);
    setPrivateCameraEnabled(false);
    setCreatorProfile(null);
    setFollowing(false);
    setRecoveredRoomId(null);
    setConnectionState("idle");
    setTheatreMode(false);
    setRailTab("chat");
    seenActivityRef.current = new Set();
    reactionBaselineRef.current = {};
    momentsInitializedRef.current = false;
    for (const timer of momentTimersRef.current) window.clearTimeout(timer);
    momentTimersRef.current.clear();
  }

  async function recoverLiveState(roomId: string) {
    const inFlight = liveRecoveryRef.current.get(roomId);
    if (inFlight) return inFlight;
    const recovery = (async () => {
      const [messages, events, currentGoals, ranking, reactions, requestOptions, tips, giftCatalog, offer, vip, peek, privateSessions] = await Promise.allSettled([
        api<Chat[]>(`/live/rooms/${roomId}/chat`),
        api<LiveActivity[]>(`/live/rooms/${roomId}/activity`),
        api<LiveGoal[]>(`/live/rooms/${roomId}/goals`),
        api<Supporter[]>(`/live/rooms/${roomId}/supporters`),
        api<{ counts: Record<string, number> }>(`/live/rooms/${roomId}/reactions`),
        api<PaidRequestOption[]>(`/live/rooms/${roomId}/paid-request-options`),
        api<TipMenuItem[]>(`/live/rooms/${roomId}/tip-menu`),
        api<GiftCatalogItem[]>(`/live/rooms/${roomId}/gifts`),
        api<SnapshotOffer>(`/live/rooms/${roomId}/snapshot-offer`),
        api<LiveVipShow | null>(`/live/rooms/${roomId}/vip-show`),
        api<PrivatePeekOffer>(`/live/rooms/${roomId}/private-peek`),
        api<PrivateSession[]>("/live/private-sessions/mine"),
      ]);
      if (messages.status === "fulfilled") setChat(messages.value);
      if (events.status === "fulfilled") setActivity(events.value);
      if (currentGoals.status === "fulfilled") setGoals(currentGoals.value);
      if (ranking.status === "fulfilled") setSupporters(ranking.value);
      if (reactions.status === "fulfilled") setReactionCounts(reactions.value.counts);
      if (requestOptions.status === "fulfilled") setPaidRequestOptions(requestOptions.value);
      if (tips.status === "fulfilled") {
        setTipMenu(tips.value);
        setSelectedTipId((current) => tips.value.some((item) => item.id === current) ? current : tips.value[0]?.id ?? "");
      }
      if (giftCatalog.status === "fulfilled") {
        setGifts(giftCatalog.value);
        setSelectedGiftId((current) => giftCatalog.value.some((item) => item.id === current) ? current : giftCatalog.value[0]?.id ?? "");
      }
      if (offer.status === "fulfilled") setSnapshotOffer(offer.value);
      if (vip.status === "fulfilled") setVipShow(vip.value);
      if (peek.status === "fulfilled") setPeekOffer(peek.value);
      if (privateSessions.status === "fulfilled") {
        setPrimaryPrivateSession(
          privateSessions.value.find(
            (session) =>
              session.participant_role === "payer" &&
              session.public_live_room_id === roomId,
          ) ?? null,
        );
      }
      if (events.status === "fulfilled" && reactions.status === "fulfilled") {
        ingestRoomMoments(events.value, reactions.value.counts);
      }
      setRecoveredRoomId(roomId);
    })();
    liveRecoveryRef.current.set(roomId, recovery);
    try {
      await recovery;
    } finally {
      if (liveRecoveryRef.current.get(roomId) === recovery) liveRecoveryRef.current.delete(roomId);
    }
  }

  async function join(room: RoomSummary) {
    if (!requireLogin()) return;
    if (!room.compliance_allowed) {
      setBlockedAccess(room);
      return;
    }
    try {
      disconnect();
      setConnectionState("connecting");
      setError("");
      const vip = await api<LiveVipShow | null>(`/live/rooms/${room.id}/vip-show`);
      if (vip?.status === "active" && !vip.viewer_admitted) {
        setVipShow(vip);
        setVipGateRoom(room);
        return;
      }
      const peek = await api<PrivatePeekOffer>(`/live/rooms/${room.id}/private-peek`);
      if (peek.paused) {
        setActive(room);
        activeRoomRef.current = room;
        setLiveEnded(false);
        setPeekOffer(peek);
        await recoverLiveState(room.id);
        if (peek.viewer_admitted) await connectPrivatePeek(room);
        return;
      }
      setVipGateRoom(null);
      const authorization = await api<Token>(`/live/rooms/${room.id}/token`, { method: "POST" });
      const livekitRoom = new Room();
      const attachMedia = (track: Track) => {
        if (!videoRef.current || attachedTracksRef.current.has(track)) return;
        attachedTracksRef.current.add(track);
        videoRef.current.append(track.attach());
      };
      livekitRoom.on(RoomEvent.TrackSubscribed, attachMedia);
      livekitRoom.on(RoomEvent.TrackUnsubscribed, (track) => {
        attachedTracksRef.current.delete(track);
        track.detach().forEach((element) => element.remove());
      });
      livekitRoom.on(RoomEvent.Reconnecting, () => setConnectionState("reconnecting"));
      livekitRoom.on(RoomEvent.Reconnected, () => setConnectionState("live"));
      await livekitRoom.connect(authorization.provider_url, authorization.token, { autoSubscribe: true });
      roomRef.current = livekitRoom;
      setActive(room);
      activeRoomRef.current = room;
      setLiveEnded(false);
      setConnectionState("live");
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      for (const participant of livekitRoom.remoteParticipants.values()) {
        for (const publication of participant.trackPublications.values()) {
          if (publication.track) attachMedia(publication.track);
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

  async function connectPrivatePeek(room: RoomSummary) {
    roomRef.current?.disconnect();
    videoRef.current?.querySelectorAll("audio, video").forEach((element) => element.remove());
    const authorization = await api<Token>(`/live/rooms/${room.id}/private-peek/token`, { method: "POST" });
    const livekitRoom = new Room();
    const attachMedia = (track: Track) => {
      if (!videoRef.current || attachedTracksRef.current.has(track)) return;
      attachedTracksRef.current.add(track);
      videoRef.current.append(track.attach());
    };
    livekitRoom.on(RoomEvent.TrackSubscribed, attachMedia);
    livekitRoom.on(RoomEvent.TrackUnsubscribed, (track) => track.detach().forEach((element) => element.remove()));
    await livekitRoom.connect(authorization.provider_url, authorization.token, { autoSubscribe: true });
    roomRef.current = livekitRoom;
    setConnectedToPeek(true);
  }

  async function connectPrimaryPrivate(session: PrivateSession) {
    if (!active || connectedToPrivate) return;
    roomRef.current?.disconnect();
    videoRef.current?.querySelectorAll("audio, video").forEach((element) => element.remove());
    const authorization = await api<Token>(`/live/private-sessions/${session.id}/token`, { method: "POST" });
    const privateRoom = new Room();
    const attachMedia = (track: Track) => {
      if (!videoRef.current || attachedTracksRef.current.has(track)) return;
      attachedTracksRef.current.add(track);
      videoRef.current.append(track.attach());
    };
    privateRoom.on(RoomEvent.TrackSubscribed, attachMedia);
    privateRoom.on(RoomEvent.TrackUnsubscribed, (track) => track.detach().forEach((element) => element.remove()));
    await privateRoom.connect(authorization.provider_url, authorization.token, { autoSubscribe: true });
    roomRef.current = privateRoom;
    setConnectedToPrivate(true);
    setConnectedToPeek(false);
    setCommerceMessage("You are in your private session. Your camera and microphone stay off until you choose to enable them.");
  }

  async function authorizePrimaryPrivate() {
    if (!primaryPrivateSession?.payment_attempt_id || commercePending) return;
    setCommercePending(true);
    try {
      const confirmed = await completePaymentCheckout(primaryPrivateSession.payment_attempt_id);
      if (!confirmed) {
        setCommerceMessage("Payment authorization is pending. Private access remains locked.");
        return;
      }
      await recoverLiveState(active!.id);
      setCommerceMessage("Payment authorized. Moving you into the private room…");
    } catch (caught) {
      setCommerceMessage(caught instanceof ApiError ? caught.message : "Unable to authorize the private session");
    } finally {
      setCommercePending(false);
    }
  }

  async function togglePrivateCamera() {
    const privateRoom = roomRef.current;
    if (!privateRoom || !connectedToPrivate) return;
    const next = !privateCameraEnabled;
    await privateRoom.localParticipant.setCameraEnabled(next);
    await privateRoom.localParticipant.setMicrophoneEnabled(next);
    setPrivateCameraEnabled(next);
  }

  async function endPrimaryPrivate() {
    if (!primaryPrivateSession) return;
    await api(`/live/private-sessions/${primaryPrivateSession.id}/end`, { method: "POST" });
    setCommerceMessage("Ending private session. The public Live will resume automatically…");
  }

  async function buyPrivatePeek() {
    if (!active || commercePending || !peekOffer?.enabled) return;
    setCommercePending(true);
    setCommerceMessage("");
    try {
      const charge = await api<LiveCommerce>(`/live/rooms/${active.id}/private-peek`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      const confirmed = await completePaymentCheckout(charge.payment_attempt_id);
      if (!confirmed) {
        setCommerceMessage("Payment is pending. The private stream remains locked.");
        return;
      }
      const refreshed = await api<PrivatePeekOffer>(`/live/rooms/${active.id}/private-peek`);
      setPeekOffer(refreshed);
      await connectPrivatePeek(active);
      setCommerceMessage("Payment confirmed. You now have view-only access for this private session.");
    } catch (caught) {
      setCommerceMessage(caught instanceof ApiError ? caught.message : "Unable to unlock this private peek");
    } finally {
      setCommercePending(false);
    }
  }

  async function buyVipAdmission(room: RoomSummary) {
    if (commercePending) return;
    setCommercePending(true);
    setCommerceMessage("");
    try {
      const charge = await api<LiveCommerce>(`/live/rooms/${room.id}/vip-show/admission`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      const confirmed = await completePaymentCheckout(charge.payment_attempt_id);
      if (!confirmed) {
        setCommerceMessage("Payment is still pending. VIP admission unlocks only after provider confirmation.");
        return;
      }
      const vip = await api<LiveVipShow>(`/live/rooms/${room.id}/vip-show`);
      setVipShow(vip);
      setCommerceMessage("VIP admission confirmed.");
      setActionPanel(null);
      if (vip.status === "active" && vip.viewer_admitted) {
        setVipGateRoom(null);
        await join(room);
      }
    } catch (caught) {
      setCommerceMessage(caught instanceof ApiError ? caught.message : "Unable to buy VIP admission");
    } finally {
      setCommercePending(false);
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
        : [...current, posted]);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to send chat");
    }
  }

  async function requestPrivateSession(mode: "one_to_one" | "two_to_one" = "one_to_one") {
    if (!active || !requireLogin() || privateRequesting) return;
    if (mode === "two_to_one" && !privateInviteeId) return;
    setPrivateRequesting(true);
    setError("");
    try {
      const request = await api<PrivateRequest>(`/live/creators/${active.creator_id}/private-requests`, {
        method: "POST",
        body: JSON.stringify({
          mode,
          ...(mode === "two_to_one" ? { invited_user_id: privateInviteeId } : {}),
        }),
      });
      setPrivateRequest(request);
      setActionPanel(null);
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
      reactionBaselineRef.current = result.counts;
      const reaction = LIVE_REACTION_VISUALS.find((item) => item.type === reactionType);
      if (reaction) {
        showStageEffect({
          kind: "reaction",
          symbol: reaction.symbol,
          title: reaction.label,
          detail: `${result.counts[reaction.type] ?? 0} total`,
        });
      }
      setActionPanel(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to react right now");
    }
  }

  function toggleActionPanel(panel: LiveActionPanel) {
    setCommerceMessage("");
    setReportMessage("");
    setActionPanel((current) => current === panel ? null : panel);
  }

  async function purchaseLiveItem(
    path: string,
    body: Record<string, unknown>,
    successMessage: string,
  ): Promise<boolean> {
    if (!active || commercePending) return false;
    setCommercePending(true);
    setCommerceMessage("");
    try {
      const charge = await api<LiveCommerce>(path, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify(body),
      });
      const confirmed = await completePaymentCheckout(charge.payment_attempt_id);
      if (!confirmed) {
        setCommerceMessage("Payment is still pending. The action will complete only after provider confirmation.");
        return false;
      }
      setCommerceMessage(successMessage);
      setActionPanel(null);
      await recoverLiveState(active.id);
      return true;
    } catch (caught) {
      setCommerceMessage(caught instanceof ApiError ? caught.message : "Unable to complete this Live purchase");
      return false;
    } finally {
      setCommercePending(false);
    }
  }

  async function toggleFavorite() {
    if (!active || !requireLogin()) return;
    try {
      const result = await api<{ following: boolean }>(`/social/creator/${active.creator_id}/follow`, {
        method: following ? "DELETE" : "POST",
      });
      setFollowing(result.following);
      setCommerceMessage(result.following ? "Creator added to favorites." : "Creator removed from favorites.");
      setActionPanel(null);
    } catch (caught) {
      setCommerceMessage(caught instanceof ApiError ? caught.message : "Unable to update favorites");
    }
  }

  function captureCurrentFrame(): Promise<Blob> {
    return new Promise((resolve, reject) => {
      const video = videoRef.current?.querySelector("video");
      if (!video || !video.videoWidth || !video.videoHeight) {
        reject(new Error("The live video is not ready for a snapshot"));
        return;
      }
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("Snapshot capture failed")), "image/png");
    });
  }

  async function takePaidSnapshot() {
    if (!active || !snapshotOffer?.enabled) return;
    try {
      const image = await captureCurrentFrame();
      const paid = await purchaseLiveItem(
        `/live/rooms/${active.id}/snapshots`, {}, "Payment confirmed. Your snapshot is downloading.",
      );
      if (!paid) return;
      const url = URL.createObjectURL(image);
      const link = document.createElement("a");
      link.href = url;
      link.download = `fanbackstage-live-${active.public_id}.png`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setCommerceMessage(caught instanceof Error ? caught.message : "Unable to capture this snapshot");
    }
  }

  function togglePlaybackMute() {
    const next = !playbackMuted;
    videoRef.current?.querySelectorAll("audio, video").forEach((element) => {
      (element as HTMLMediaElement).muted = next;
    });
    setPlaybackMuted(next);
  }

  function changePlaybackVolume(value: number) {
    videoRef.current?.querySelectorAll("audio, video").forEach((element) => {
      const media = element as HTMLMediaElement;
      media.volume = value;
      media.muted = value === 0;
    });
    setPlaybackVolume(value);
    setPlaybackMuted(value === 0);
  }

  async function sendTip(item: TipMenuItem) {
    if (!active) return;
    await purchaseLiveItem(
      `/live/rooms/${active.id}/tips`,
      { tip_catalog_item_id: item.id },
      `${item.label} tip sent successfully.`,
    );
  }

  async function sendGift(item: GiftCatalogItem) {
    if (!active) return;
    await purchaseLiveItem(
      `/live/rooms/${active.id}/gifts`,
      { gift_catalog_item_id: item.id },
      `${item.name} sent successfully.`,
    );
  }

  async function submitReport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    setReportMessage("");
    try {
      await api(`/live/rooms/${active.id}/reports`, {
        method: "POST",
        body: JSON.stringify({
          reason: values.get("report-reason"),
          details: values.get("report-details"),
        }),
      });
      form.reset();
      setReportMessage("Report received. Our Trust & Safety team will review it.");
      setActionPanel(null);
    } catch (caught) {
      setReportMessage(caught instanceof ApiError ? caught.message : "Unable to submit this report");
    }
  }

  async function submitPaidRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active || commercePending) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    setCommercePending(true);
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
      setActionPanel(null);
      await recoverLiveState(active.id);
    } catch (caught) {
      setCommerceMessage(caught instanceof ApiError ? caught.message : "Unable to send paid request");
    } finally {
      setCommercePending(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      await refresh();
      if (!cancelled) timer = window.setTimeout(() => void poll(), 15_000);
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
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
    if (
      !active ||
      (!connectedToPeek && !connectedToPrivate) ||
      !peekOffer ||
      peekOffer.paused
    )
      return;
    void join(active);
  }, [active?.id, connectedToPeek, connectedToPrivate, peekOffer?.paused]);

  useEffect(() => {
    if (
      !primaryPrivateSession ||
      connectedToPrivate ||
      !["ready", "connecting", "active", "reconnecting"].includes(
        primaryPrivateSession.status,
      )
    )
      return;
    void connectPrimaryPrivate(primaryPrivateSession).catch((caught) => {
      setCommerceMessage(
        caught instanceof ApiError
          ? caught.message
          : "Unable to enter the private session",
      );
    });
  }, [primaryPrivateSession, connectedToPrivate]);

  const roomCreator = useMemo(() => active ? identityFor(active, creators) : null, [active, creators]);
  const visibleRooms = useMemo(() => {
    const query = directoryQuery.trim().toLocaleLowerCase();
    const filtered = rooms.filter((room) => {
      if (directoryFilter !== "all" && room.access_mode !== directoryFilter) return false;
      if (!query) return true;
      const identity = identityFor(room, creators);
      return `${room.title} ${identity.displayName} ${identity.username ?? ""}`.toLocaleLowerCase().includes(query);
    });
    return directorySort === "viewers"
      ? [...filtered].sort((left, right) => right.viewer_count - left.viewer_count)
      : filtered;
  }, [creators, directoryFilter, directoryQuery, directorySort, rooms]);
  const liveActionItems: [LiveActionPanel, string][] = [
    ["bio", "Creator bio"],
    ["favorite", following ? "Remove favorite" : "Add to favorites"],
    ["subscribe", "Subscribe"],
    ["react", "React"],
    ...(tipMenu.length ? [["tip", "Send a tip"]] as [LiveActionPanel, string][] : []),
    ...(gifts.length ? [["gift", "Send a gift"]] as [LiveActionPanel, string][] : []),
    ...(paidRequestOptions.length ? [["paid-request", "Send a paid request"]] as [LiveActionPanel, string][] : []),
    ...(snapshotOffer?.enabled ? [["snapshot", "Take a paid snapshot"]] as [LiveActionPanel, string][] : []),
    ...(vipShow && !["cancelled", "completed"].includes(vipShow.status) ? [["vip", "VIP show"]] as [LiveActionPanel, string][] : []),
    ["private", "Request a private session"],
    ["settings", "Playback settings"],
    ["report", "Report this live"],
  ];

  useEffect(() => {
    if (!active || !roomCreator?.username) return;
    void api<PublicCreator>(`/creators/${encodeURIComponent(roomCreator.username)}`)
      .then(setCreatorProfile)
      .catch(() => undefined);
    void api<{ following: boolean }>(`/social/creator/${active.creator_id}/follow-state`)
      .then((state) => setFollowing(state.following))
      .catch(() => setFollowing(false));
    void api<PrivateInviteCandidate[]>(`/live/creators/${active.creator_id}/private-invite-candidates`)
      .then((items) => {
        setPrivateInviteCandidates(items);
        setPrivateInviteeId((current) => current || items[0]?.user_id || "");
      })
      .catch(() => setPrivateInviteCandidates([]));
  }, [active, roomCreator?.username]);

  useEffect(() => {
    if (!active || liveEnded) return;
    let cancelled = false;
    let timer: number | undefined;
    const recover = async () => {
      try {
        if (!cancelled) await recoverLiveState(active.id);
      } catch {
        // Durable Live projections are retried together. A transient API
        // failure must not disconnect otherwise healthy LiveKit media.
      } finally {
        if (!cancelled) timer = window.setTimeout(() => void recover(), 3_000);
      }
    };
    void recover();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [active, liveEnded]);

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

      {vipGateRoom && vipShow?.status === "active" && (
        <div className={styles.liveActionModalLayer}>
          <section aria-label="VIP admission required" aria-modal="true" className={styles.liveActionPanel} role="dialog">
            <header><div><span>VIP SHOW</span><strong>{vipShow.title}</strong></div><button aria-label="Close VIP admission" onClick={() => { setVipGateRoom(null); setVipShow(null); }} type="button">×</button></header>
            <div className={styles.livePrivateAction}>
              <p>{vipShow.description}</p>
              <p>The VIP segment is live. Confirm the creator-set admission price to enter.</p>
              <strong>{formatMoney(vipShow.buy_in_amount_minor, vipShow.currency)}</strong>
              <button disabled={commercePending} onClick={() => void buyVipAdmission(vipGateRoom)} type="button">{commercePending ? "Processing…" : "Pay and join VIP"}</button>
              {commerceMessage && <p role="status">{commerceMessage}</p>}
            </div>
          </section>
        </div>
      )}

      {active && roomCreator && (
        <section
          aria-busy={recoveredRoomId !== active.id}
          aria-label={`Watching ${active.title}`}
          className={`${styles.liveViewer} ${theatreMode ? styles.liveViewerTheatre : ""}`}
          data-live-state={recoveredRoomId === active.id ? "recovered" : "recovering"}
        >
          <h2 className="sr-only">Watching: {active.title}</h2>
          <div aria-label="Live video" className={styles.liveStage}>
            <div aria-hidden="true" className={styles.liveMediaMount} ref={videoRef} />
            <div className={styles.liveStagePlaceholder}><span aria-hidden="true">▶</span><p>Connecting to the creator&apos;s protected live stream…</p></div>
            {liveEnded && (
              <section
                aria-label="Creator is offline"
                aria-live="assertive"
                className={styles.liveEndedScreen}
                style={{ backgroundImage: `linear-gradient(180deg, rgba(3,5,12,.28), rgba(3,5,12,.95)), url(${mediaForUsername(roomCreator.username)?.cover ?? creatorProfile?.cover_reference ?? ""})` }}
              >
                <div>
                  <span>LIVE ENDED</span>
                  <h3>{roomCreator.displayName} is offline</h3>
                  <p>This broadcast has finished. Follow the creator for the next show or continue browsing live rooms.</p>
                  <nav>
                    {roomCreator.username && <Link href={`/creator/${roomCreator.username}`}>View creator profile</Link>}
                    <button onClick={disconnect} type="button">Browse live creators</button>
                  </nav>
                </div>
              </section>
            )}
            <div className={styles.liveStageHeader}>
              <CreatorAvatar displayName={roomCreator.displayName} live size={42} username={roomCreator.username} />
              <span><strong>{roomCreator.displayName}</strong>{roomCreator.username && <small>@{roomCreator.username}</small>}</span>
            </div>
            <div aria-live="polite" className={`${styles.liveConnectionState} ${connectionState === "reconnecting" ? styles.liveConnectionWarning : ""}`}>
              <i aria-hidden="true" />
              {connectionState === "reconnecting" ? "Reconnecting…" : connectionState === "connecting" ? "Connecting…" : connectionState === "ended" ? "Live ended" : "Live"}
              <span>{active.viewer_count} watching</span>
            </div>
            {primaryPrivateSession?.status === "awaiting_payment_authorization" && (
              <section
                aria-label="Authorize private session"
                aria-modal="true"
                className={styles.livePrivatePaymentPrompt}
                role="dialog"
              >
                <span>PRIVATE SESSION ACCEPTED</span>
                <h3>{roomCreator.displayName} is ready for your private session.</h3>
                <p>
                  Authorize up to {formatMoney(primaryPrivateSession.max_authorization_minor, primaryPrivateSession.currency)}.
                  Final settlement uses verified billable time, subject to the displayed minimum.
                </p>
                {primaryPrivateSession.peeks_allowed ? (
                  <p>
                    Disclosure: this session allows other verified viewers to buy silent,
                    view-only peek access. They cannot use private chat, microphone, or camera.
                  </p>
                ) : (
                  <p>No paid peek viewers are allowed for this session.</p>
                )}
                <button disabled={commercePending} onClick={() => void authorizePrimaryPrivate()} type="button">
                  {commercePending ? "Authorizing…" : "Authorize payment and enter private"}
                </button>
              </section>
            )}
            {peekOffer?.paused && !connectedToPeek && !connectedToPrivate && (
              <section
                aria-label="Private session holding screen"
                className={styles.livePrivateHolding}
                style={{ backgroundImage: `linear-gradient(180deg, rgba(4,6,17,.25), rgba(4,6,17,.92)), url(${mediaForUsername(roomCreator.username)?.cover ?? creatorProfile?.cover_reference ?? ""})` }}
              >
                <div>
                  <span>PRIVATE SESSION IN PROGRESS</span>
                  <h3>{roomCreator.displayName} will return to public Live afterwards.</h3>
                  {peekOffer.enabled && peekOffer.amount_minor !== null && peekOffer.currency ? (
                    <>
                      <p>Watch the private video as a silent, view-only guest. Private chat and viewer cameras remain hidden.</p>
                      <button disabled={commercePending} onClick={() => void buyPrivatePeek()} type="button">
                        {commercePending ? "Processing…" : `Take a peek · ${formatMoney(peekOffer.amount_minor, peekOffer.currency)}`}
                      </button>
                    </>
                  ) : <p>This creator has disabled paid peeks. Stay here and the public Live will resume automatically.</p>}
                </div>
              </section>
            )}
            {connectedToPeek && <div className={styles.livePeekBadge}>PAID PEEK · VIEW ONLY</div>}
            {connectedToPrivate && (
              <div className={styles.livePrivateParticipantControls}>
                <strong>YOUR PRIVATE SESSION</strong>
                <button onClick={() => void togglePrivateCamera()} type="button">
                  {privateCameraEnabled ? "Turn camera & mic off" : "Turn camera & mic on"}
                </button>
                <button onClick={() => void endPrimaryPrivate()} type="button">
                  End private
                </button>
              </div>
            )}
            {vipShow && !["cancelled", "completed"].includes(vipShow.status) && (
              <div className={styles.liveVipBanner}><strong>VIP · {vipShow.title}</strong><span>{vipShow.status === "active" ? "VIP show live" : `${formatMoney(vipShow.confirmed_amount_minor, vipShow.currency)} / ${formatMoney(vipShow.goal_amount_minor, vipShow.currency)}`}</span></div>
            )}
            <nav aria-label="Live actions" className={styles.liveActionDock}>
              {liveActionItems.map(([kind, label]) => (
                <button
                  aria-expanded={actionPanel === kind}
                  aria-haspopup="dialog"
                  aria-label={label}
                  className={actionPanel === kind ? styles.liveActionActive : undefined}
                  key={kind}
                  onClick={() => toggleActionPanel(kind)}
                  title={label}
                  type="button"
                >
                  <ActionIcon kind={kind} />
                  <span>{label}</span>
                </button>
              ))}
            </nav>
            {actionPanel && (
              <div className={styles.liveActionModalLayer} onMouseDown={() => setActionPanel(null)}>
              <section
                aria-label={`${actionPanel} controls`}
                aria-modal="true"
                className={styles.liveActionPanel}
                onKeyDown={(event) => { if (event.key === "Escape") setActionPanel(null); }}
                onMouseDown={(event) => event.stopPropagation()}
                role="dialog"
              >
                <header>
                  <div>
                    <span>LIVE ACTION</span>
                    <strong>{
                      actionPanel === "react" ? "React to the show"
                        : actionPanel === "bio" ? `${roomCreator.displayName}'s bio`
                          : actionPanel === "favorite" ? "Favorite creator"
                            : actionPanel === "subscribe" ? "Subscribe to creator"
                        : actionPanel === "tip" ? "Send a tip"
                          : actionPanel === "gift" ? "Send a gift"
                            : actionPanel === "paid-request" ? "Paid request"
                              : actionPanel === "snapshot" ? "Take a snapshot"
                                : actionPanel === "vip" ? "VIP show"
                                  : actionPanel === "private" ? "Private session"
                                  : actionPanel === "settings" ? "Playback settings"
                                    : "Report this live"
                    }</strong>
                  </div>
                  <button aria-label="Close live action" onClick={() => setActionPanel(null)} type="button">×</button>
                </header>

                {actionPanel === "react" && (
                  <div aria-label="Live reactions" className={styles.liveReactionPicker}>
                    {LIVE_REACTION_VISUALS.map((reaction) => (
                      <button
                        aria-label={`React ${reaction.label}`}
                        key={reaction.type}
                        onClick={() => void react(reaction.type)}
                        type="button"
                      >
                        <span aria-hidden="true">{reaction.symbol}</span>
                        <strong>{reaction.label}</strong>
                        <small>{reactionCounts[reaction.type] ?? 0}</small>
                      </button>
                    ))}
                  </div>
                )}

                {actionPanel === "bio" && (
                  <div className={styles.livePrivateAction}>
                    <p>{creatorProfile?.bio || "This creator has not added a bio yet."}</p>
                    {creatorProfile?.languages.length ? <small>Languages: {creatorProfile.languages.map((item) => item.label).join(", ")}</small> : null}
                    {roomCreator.username && <Link href={`/creator/${roomCreator.username}`}>View full profile</Link>}
                  </div>
                )}

                {actionPanel === "favorite" && (
                  <div className={styles.livePrivateAction}>
                    <p>{following ? "This creator is in your favorites." : "Favorite this creator to find their next live quickly."}</p>
                    <button onClick={() => void toggleFavorite()} type="button">{following ? "Remove from favorites" : "Add to favorites"}</button>
                  </div>
                )}

                {actionPanel === "subscribe" && roomCreator.username && (
                  <SubscriptionOptions creatorId={active.creator_id} username={roomCreator.username} />
                )}

                {actionPanel === "vip" && (
                  <div className={styles.livePrivateAction}>
                    {!vipShow || vipShow.status === "cancelled" || vipShow.status === "completed" ? (
                      <p>No VIP show is currently scheduled.</p>
                    ) : (
                      <>
                        <p className="eyebrow">{vipShow.status.replace("_", " ").toUpperCase()}</p>
                        <h3>{vipShow.title}</h3>
                        <p>{vipShow.description}</p>
                        <p><strong>{formatMoney(vipShow.confirmed_amount_minor, vipShow.currency)}</strong> of {formatMoney(vipShow.goal_amount_minor, vipShow.currency)} pledged.</p>
                        <progress aria-label="VIP goal progress" max={vipShow.goal_amount_minor} value={vipShow.confirmed_amount_minor} />
                        <p>Admission: {formatMoney(vipShow.buy_in_amount_minor, vipShow.currency)}</p>
                        {vipShow.viewer_admitted ? <p>You&apos;re on the VIP list.</p> : <button disabled={commercePending} onClick={() => void buyVipAdmission(active)} type="button">{commercePending ? "Processing…" : "Buy VIP admission"}</button>}
                      </>
                    )}
                  </div>
                )}

                {actionPanel === "tip" && (
                  tipMenu.length ? (
                    <form aria-label="Choose a tip" onSubmit={(event) => {
                      event.preventDefault();
                      const item = tipMenu.find((tip) => tip.id === selectedTipId);
                      if (item) void sendTip(item);
                    }}>
                      <div className={styles.liveSelectedPurchase}>
                        <Image alt="" height={58} src={tipMenu.find((item) => item.id === selectedTipId)?.icon ?? tipMenu[0].icon} width={58} />
                        <span><strong>{tipMenu.find((item) => item.id === selectedTipId)?.label ?? tipMenu[0].label}</strong><small>Platform tip</small></span>
                      </div>
                      <label>
                        Tip
                        <select aria-label="Tip" onChange={(event) => setSelectedTipId(event.target.value)} value={selectedTipId}>
                          {tipMenu.map((item) => <option key={item.id} value={item.id}>{item.label} · {formatMoney(item.amount_minor, item.currency)}</option>)}
                        </select>
                      </label>
                      <button disabled={commercePending} type="submit">{commercePending ? "Processing…" : "Send tip"}</button>
                    </form>
                  ) : <p>Tips are temporarily unavailable.</p>
                )}

                {actionPanel === "gift" && (
                  gifts.length ? (
                    <form aria-label="Choose a gift" onSubmit={(event) => {
                      event.preventDefault();
                      const item = gifts.find((gift) => gift.id === selectedGiftId);
                      if (item) void sendGift(item);
                    }}>
                      <div className={styles.liveSelectedPurchase}>
                        <Image alt="" height={58} src={gifts.find((item) => item.id === selectedGiftId)?.icon ?? gifts[0].icon} width={58} />
                        <span><strong>{gifts.find((item) => item.id === selectedGiftId)?.name ?? gifts[0].name}</strong><small>Platform gift</small></span>
                      </div>
                      <label>
                        Gift
                        <select aria-label="Gift" onChange={(event) => setSelectedGiftId(event.target.value)} value={selectedGiftId}>
                          {gifts.map((item) => <option key={item.id} value={item.id}>{item.name} · {formatMoney(item.amount_minor, item.currency)}</option>)}
                        </select>
                      </label>
                      <button disabled={commercePending} type="submit">{commercePending ? "Processing…" : "Send gift"}</button>
                    </form>
                  ) : <p>No gifts are available for this room.</p>
                )}

                {actionPanel === "paid-request" && (
                  paidRequestOptions.length ? (
                    <form aria-label="Send a paid request" onSubmit={submitPaidRequest}>
                      <label>
                        Request
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
                      <button disabled={commercePending} type="submit">Pay and send request</button>
                    </form>
                  ) : <p>This creator has no paid requests available right now.</p>
                )}

                {actionPanel === "private" && (
                  <div className={styles.livePrivateAction}>
                    <p>Request a private 1:1. If accepted and authorised, the public show pauses and resumes when your session ends.</p>
                    {privateRequest ? (
                      <p className={styles.liveInteractionSuccess} role="status">
                        Private 1:1 queued. {roomCreator.displayName} must accept before payment authorisation.
                      </p>
                    ) : (
                      <div className={styles.livePrivateRequestChoices}>
                        <button className={styles.privateRequestButton} disabled={privateRequesting} onClick={() => void requestPrivateSession()} type="button">
                          {privateRequesting ? "Requesting…" : "Request private 1:1"}
                        </button>
                        {privateInviteCandidates.length > 0 && (
                          <>
                            <label>
                              Second fan for 2-to-1
                              <select aria-label="Second fan for 2-to-1" onChange={(event) => setPrivateInviteeId(event.target.value)} value={privateInviteeId}>
                                {privateInviteCandidates.map((candidate) => <option key={candidate.user_id} value={candidate.user_id}>{candidate.label}</option>)}
                              </select>
                            </label>
                            <button disabled={privateRequesting || !privateInviteeId} onClick={() => void requestPrivateSession("two_to_one")} type="button">Request private 2-to-1</button>
                            <small>The invited fan must accept first and has no payment responsibility.</small>
                          </>
                        )}
                      </div>
                    )}
                    <small>
                      {privateRequest
                        ? `Minimum ${formatMoney(privateRequest.minimum_charge_minor, privateRequest.currency)} · ${formatMoney(privateRequest.per_minute_price_minor, privateRequest.currency)}/minute`
                        : "The creator confirms availability before any payment is authorised."}
                    </small>
                    {privateRequest?.peeks_may_be_available && <small>Disclosure: this creator allows other verified viewers to buy silent, view-only peek access. They cannot see your chat, microphone, or camera.</small>}
                  </div>
                )}

                {actionPanel === "snapshot" && (
                  <div className={styles.livePrivateAction}>
                    {snapshotOffer?.enabled ? <>
                      <p>Capture the current video frame after payment is confirmed. The image is created locally and is not stored by FanBackstage.</p>
                      <button disabled={commercePending} onClick={() => void takePaidSnapshot()} type="button">
                        {commercePending ? "Processing…" : `Pay ${formatMoney(snapshotOffer.amount_minor, snapshotOffer.currency)} and capture`}
                      </button>
                    </> : <p>This creator has not enabled paid snapshots.</p>}
                  </div>
                )}

                {actionPanel === "settings" && (
                  <div className={styles.livePrivateAction}>
                    <button onClick={togglePlaybackMute} type="button">{playbackMuted ? "Unmute live" : "Mute live"}</button>
                    <button onClick={() => void videoRef.current?.requestFullscreen()} type="button">View fullscreen</button>
                    <button onClick={() => {
                      const video = videoRef.current?.querySelector("video");
                      if (video && document.pictureInPictureEnabled) void video.requestPictureInPicture();
                    }} type="button">Picture in picture</button>
                  </div>
                )}

                {actionPanel === "report" && (
                  <form aria-label="Report this live" onSubmit={submitReport}>
                    <label>
                      Reason
                      <select defaultValue="" name="report-reason" required>
                        <option disabled value="">Choose a reason</option>
                        <option value="harassment">Harassment</option>
                        <option value="non_consensual_content">Non-consensual content</option>
                        <option value="underage_concern">Underage concern</option>
                        <option value="illegal_content">Illegal content</option>
                        <option value="scam_fraud">Scam or fraud</option>
                        <option value="privacy">Privacy concern</option>
                        <option value="other">Other</option>
                      </select>
                    </label>
                    <label>
                      Details
                      <textarea maxLength={1000} name="report-details" />
                    </label>
                    <button type="submit">Send report</button>
                  </form>
                )}
              </section>
              </div>
            )}
            {tipMenu.length > 0 && (
              <nav aria-label="Quick tips" className={styles.liveQuickTips}>
                <span>Quick tip</span>
                {hoveredTip && <output className={styles.liveQuickTipTooltip} id="live-quick-tip-tooltip" role="tooltip" style={{ left: hoveredTipLeft ?? "50%" }}>{hoveredTip.label} · {formatMoney(hoveredTip.amount_minor, hoveredTip.currency)}</output>}
                <div onScroll={hideTipTooltip}>
                  {tipMenu.map((item) => (
                    <button
                      aria-describedby={hoveredTip?.id === item.id ? "live-quick-tip-tooltip" : undefined}
                      aria-label={`Choose ${item.label} tip for ${formatMoney(item.amount_minor, item.currency)}`}
                      aria-haspopup="dialog"
                      data-tooltip={`${item.label} · ${formatMoney(item.amount_minor, item.currency)}`}
                      key={item.id}
                      onBlur={hideTipTooltip}
                      onClick={() => { setSelectedTipId(item.id); setActionPanel("tip"); }}
                      onFocus={(event) => showTipTooltip(item, event.currentTarget)}
                      onMouseEnter={(event) => showTipTooltip(item, event.currentTarget)}
                      onMouseLeave={hideTipTooltip}
                      title={`${item.label} · ${formatMoney(item.amount_minor, item.currency)}`}
                      type="button"
                    >
                      <Image alt="" height={44} src={item.icon} width={44} />
                      <span className="sr-only">{item.label} · {formatMoney(item.amount_minor, item.currency)}</span>
                    </button>
                  ))}
                </div>
              </nav>
            )}
            <div aria-label="Video controls" className={styles.liveVideoControls} role="group">
              <button aria-label={playbackMuted ? "Unmute live" : "Mute live"} onClick={togglePlaybackMute} type="button">{playbackMuted ? "Muted" : "Sound"}</button>
              <label><span className="sr-only">Live volume</span><input aria-label="Live volume" max="1" min="0" onChange={(event) => changePlaybackVolume(Number(event.target.value))} step="0.05" type="range" value={playbackVolume} /></label>
              <button aria-pressed={theatreMode} onClick={() => setTheatreMode((current) => !current)} type="button">Theatre</button>
              <button onClick={() => void videoRef.current?.parentElement?.requestFullscreen()} type="button">Fullscreen</button>
            </div>
            {(commerceMessage || reportMessage) && (
              <p className={styles.liveStageMessage} role="status">{commerceMessage || reportMessage}</p>
            )}
            <LiveStageMoments effects={stageEffects} reactionCounts={reactionCounts} />
            <button className={styles.leaveLive} onClick={disconnect} type="button">Leave live</button>
          </div>
          <aside className={styles.liveChat}>
            <div className={styles.liveChatHeader}>
              <h2>Live chat</h2>
              <span>{active.viewer_count} watching</span>
              {roomCreator.username && <Link className={styles.liveProfileLink} href={`/creator/${roomCreator.username}`}>View profile</Link>}
            </div>
            <div aria-label="Live room panels" className={styles.liveRailTabs} role="tablist">
              {(["chat", "activity", "supporters"] as const).map((tab) => (
                <button aria-selected={railTab === tab} key={tab} onClick={() => setRailTab(tab)} role="tab" type="button">
                  {tab === "supporters" ? "Top" : tab}
                </button>
              ))}
            </div>
            <section aria-label="Live room highlights" className={styles.liveChatContext}>
              {railTab === "supporters" && supporters.length > 0 && (
                <section aria-label="Top supporters" className={styles.liveSupporters}>
                  <strong>Top supporters</strong>
                  <ol>
                    {supporters.slice(0, 3).map((supporter) => (
                      <li key={`${supporter.rank}-${supporter.supporter_label}`}>
                        <span><b>{supporter.rank}</b>{supporter.supporter_label}</span>
                        <strong>{formatMoney(supporter.amount_minor, supporter.currency)}</strong>
                      </li>
                    ))}
                  </ol>
                </section>
              )}
              {goals.map((goal) => (
                <section aria-label={`Goal: ${goal.title}`} className={styles.liveGoal} key={goal.id}>
                  <div><strong>{goal.title}</strong><span>{formatMoney(goal.progress_amount_minor, goal.currency)} / {formatMoney(goal.target_amount_minor, goal.currency)}</span></div>
                  <progress max={goal.target_amount_minor} value={Math.min(goal.progress_amount_minor, goal.target_amount_minor)} />
                </section>
              ))}
              {railTab === "activity" && activity.length > 0 && (
                <section aria-label="Live activity" className={styles.liveActivityTicker}>
                  {activity.map((item) => (
                    <span key={item.id}>
                      {item.event_type.replaceAll("_", " ")}
                      {item.amount_minor && item.currency ? ` · ${formatMoney(item.amount_minor, item.currency)}` : ""}
                    </span>
                  ))}
                </section>
              )}
              {railTab === "activity" && activity.length === 0 && <p className={styles.liveRailEmpty}>Confirmed room activity will appear here.</p>}
              {railTab === "supporters" && supporters.length === 0 && <p className={styles.liveRailEmpty}>Top supporters will appear after a confirmed Live purchase.</p>}
            </section>
            <div className={styles.liveChatMessages} hidden={railTab !== "chat"}>
              {chat.length ? chat.map((message) => <p key={message.id}><strong>{message.sender_label}</strong><span>{message.body}</span></p>) : <p>Be the first to say hello.</p>}
            </div>
            <form hidden={railTab !== "chat"} onSubmit={send}>
              <label className="sr-only" htmlFor="live-chat-body">Live chat message</label>
              <input id="live-chat-body" maxLength={1000} name="body" placeholder="Say something…" required />
              <button type="submit">Send</button>
            </form>
          </aside>
        </section>
      )}

      {rooms.length > 0 ? (
        <>
          <section aria-label="Live directory filters" className={styles.liveDirectoryToolbar}>
            <div role="group" aria-label="Audience filter">
              {(["all", "public", "followers", "subscribers"] as const).map((filter) => (
                <button aria-pressed={directoryFilter === filter} key={filter} onClick={() => setDirectoryFilter(filter)} type="button">{filter === "all" ? "All live" : filter}</button>
              ))}
            </div>
            <label><span className="sr-only">Search live creators</span><input onChange={(event) => setDirectoryQuery(event.target.value)} placeholder="Search creators or shows" type="search" value={directoryQuery} /></label>
            <label><span className="sr-only">Sort live creators</span><select onChange={(event) => setDirectorySort(event.target.value as "recommended" | "viewers")} value={directorySort}><option value="recommended">Recommended</option><option value="viewers">Most watched</option></select></label>
          </section>
          {visibleRooms.length > 0 ? <div className={styles.liveGrid}>
          {visibleRooms.map((room) => {
            const identity = identityFor(room, creators);
            const image = mediaForUsername(identity.username)?.portrait || "/images/fanbackstage-hero.png";
            return (
              <LiveDirectoryCard identity={identity} image={image} key={room.id} onWatch={() => void join(room)} room={room} />
            );
          })}
          </div> : <div className={styles.liveQuietNotice} role="status"><div><strong>No live rooms match these filters</strong><span>Change the audience filter or clear the search.</span></div><button className={styles.secondaryLink} onClick={() => { setDirectoryFilter("all"); setDirectoryQuery(""); }} type="button">Clear filters</button></div>}
        </>
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
