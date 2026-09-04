"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";

import { api, ApiError } from "../lib/api";
import {
  effectForActivity,
  LIVE_REACTION_VISUALS,
  LiveStageMoments,
  type LiveActivityMoment,
  type LiveStageEffect,
} from "./live-stage-moments";
import styles from "./social-surface.module.css";

type LiveRoom = {
  id: string;
  title: string;
  status: string;
  access_mode: string;
  viewer_count: number;
  peak_viewer_count: number;
  started_at: string | null;
};
type Token = { provider_url: string; token: string };
type Chat = { id: string; body: string; sender_user_id: string | null; sender_label: string };
type PostedChat = Chat;
type Supporter = { rank: number; amount_minor: number; currency: string; supporter_label: string };
type LiveGoal = { id: string; title: string; target_amount_minor: number; progress_amount_minor: number; currency: string };
type AudienceMember = { user_id: string; label: string; joined_at: string | null };
type LiveAudience = { current_viewers: number; peak_viewers: number; unique_viewers: number; members: AudienceMember[] };
type LiveSessionSummary = { financial_actions: Array<{ event_type: string; currency: string; count: number; amount_minor: number }> };
type LiveSettings = {
  private_sessions_enabled: boolean;
  one_to_one_price_minor: number;
  two_to_one_price_minor: number;
  currency: string;
  minimum_minutes: number;
  max_authorization_minor: number;
  snapshots_enabled: boolean;
  snapshot_price_minor: number;
  private_peeks_enabled: boolean;
};
type PrivatePeekPolicy = {
  active: boolean;
  amount_minor: number;
  currency: string;
  commission_basis_points: number;
};
type PrivateRequest = {
  id: string;
  mode: string;
  per_minute_price_minor: number;
  minimum_charge_minor: number;
  max_authorization_minor: number;
  currency: string;
  expires_at: string;
  invitation_status: string;
  invited_viewer_label: string | null;
};
type PrivateSession = {
  id: string;
  status: string;
  mode: string;
  per_minute_price_minor: number;
  minimum_charge_minor: number;
  currency: string;
  billable_seconds: number;
  payment_attempt_id: string | null;
  participant_role: string;
  public_live_room_id: string | null;
  peeks_allowed: boolean;
  peek_price_minor: number | null;
  peek_currency: string | null;
  peek_commission_basis_points: number | null;
};

function formatMoney(amountMinor: number, currency: string) {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
  }).format(amountMinor / 100);
}

function formatDuration(totalSeconds: number) {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}` : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function describeActivity(event: LiveActivityMoment) {
  const effect = effectForActivity(event);
  return {
    detail: effect?.detail || (event.amount_minor && event.currency ? formatMoney(event.amount_minor, event.currency) : "Live activity"),
    title: effect?.title || event.event_type.replaceAll("_", " "),
  };
}

export function LiveStudio() {
  const [room, setRoom] = useState<LiveRoom | null>(null);
  const [settings, setSettings] = useState<LiveSettings | null>(null);
  const [peekPolicy, setPeekPolicy] = useState<PrivatePeekPolicy | null>(null);
  const [message, setMessage] = useState("");
  const [roomChecked, setRoomChecked] = useState(false);
  const [publisherConnected, setPublisherConnected] = useState(false);
  const [chat, setChat] = useState<Chat[]>([]);
  const [activity, setActivity] = useState<LiveActivityMoment[]>([]);
  const [supporters, setSupporters] = useState<Supporter[]>([]);
  const [goals, setGoals] = useState<LiveGoal[]>([]);
  const [audience, setAudience] = useState<LiveAudience | null>(null);
  const [sessionSummary, setSessionSummary] = useState<LiveSessionSummary>({ financial_actions: [] });
  const [presenceNotices, setPresenceNotices] = useState<string[]>([]);
  const [reactionCounts, setReactionCounts] = useState<Record<string, number>>(
    {},
  );
  const [stageEffects, setStageEffects] = useState<LiveStageEffect[]>([]);
  const [privateRequests, setPrivateRequests] = useState<PrivateRequest[]>([]);
  const [activePrivateSession, setActivePrivateSession] =
    useState<PrivateSession | null>(null);
  const [privateConnected, setPrivateConnected] = useState(false);
  const [dismissedPrivateRequests, setDismissedPrivateRequests] = useState<
    Set<string>
  >(new Set());
  const providerRoom = useRef<Room | null>(null);
  const privateProviderRoom = useRef<Room | null>(null);
  const privateJoinInFlight = useRef(false);
  const publisherVideo = useRef<HTMLDivElement | null>(null);
  const liveActionInFlight = useRef(false);
  const [liveActionPending, setLiveActionPending] = useState(false);
  const [privateSettingsOpen, setPrivateSettingsOpen] = useState(false);
  const [endConfirmOpen, setEndConfirmOpen] = useState(false);
  const [cameraEnabled, setCameraEnabled] = useState(true);
  const [microphoneEnabled, setMicrophoneEnabled] = useState(true);
  const [connectionState, setConnectionState] = useState<"ready" | "connecting" | "live" | "reconnecting">("ready");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [studioRailTab, setStudioRailTab] = useState<"chat" | "activity">("chat");
  const [preflightReady, setPreflightReady] = useState(false);
  const [preflightError, setPreflightError] = useState("");
  const [cameraDevices, setCameraDevices] = useState<MediaDeviceInfo[]>([]);
  const [microphoneDevices, setMicrophoneDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedCamera, setSelectedCamera] = useState("");
  const [selectedMicrophone, setSelectedMicrophone] = useState("");
  const [lastSessionSummary, setLastSessionSummary] = useState<{ duration: number; viewers: number; peak: number; unique: number; tips: number; gifts: number; requests: number; snapshots: number } | null>(null);
  const preflightVideo = useRef<HTMLVideoElement | null>(null);
  const preflightStream = useRef<MediaStream | null>(null);
  const seenActivity = useRef<Set<string>>(new Set());
  const reactionBaseline = useRef<Record<string, number>>({});
  const momentsInitialized = useRef(false);
  const momentSequence = useRef(0);
  const momentTimers = useRef<Set<number>>(new Set());
  const previousAudience = useRef<Map<string, string> | null>(null);

  const sessionCommerce = (() => {
    const financial = sessionSummary.financial_actions;
    const totals = new Map<string, number>();
    for (const event of financial) {
      totals.set(event.currency, (totals.get(event.currency) ?? 0) + event.amount_minor);
    }
    return {
      tips: financial.filter((event) => event.event_type === "tip").reduce((sum, event) => sum + event.count, 0),
      gifts: financial.filter((event) => event.event_type === "gift").reduce((sum, event) => sum + event.count, 0),
      requests: financial.filter((event) => event.event_type === "paid_request").reduce((sum, event) => sum + event.count, 0),
      snapshots: financial.filter((event) => event.event_type === "snapshot").reduce((sum, event) => sum + event.count, 0),
      totals: [...totals.entries()],
    };
  })();

  function ingestAudience(next: LiveAudience) {
    const current = new Map(next.members.map((member) => [member.user_id, member.label]));
    if (previousAudience.current) {
      const notices: string[] = [];
      for (const [id, label] of current) if (!previousAudience.current.has(id)) notices.push(`${label} joined`);
      for (const [id, label] of previousAudience.current) if (!current.has(id)) notices.push(`${label} left`);
      if (notices.length) setPresenceNotices((existing) => [...existing, ...notices].slice(-6));
    }
    previousAudience.current = current;
    setAudience(next);
  }

  function showStageEffect(effect: Omit<LiveStageEffect, "id">) {
    const id = `${Date.now()}-${++momentSequence.current}`;
    setStageEffects((current) => [...current.slice(-5), { ...effect, id }]);
    const timer = window.setTimeout(() => {
      setStageEffects((current) => current.filter((item) => item.id !== id));
      momentTimers.current.delete(timer);
    }, 4_800);
    momentTimers.current.add(timer);
  }

  function ingestMoments(
    events: LiveActivityMoment[],
    counts: Record<string, number>,
  ) {
    if (!momentsInitialized.current) {
      seenActivity.current = new Set(events.map((event) => event.id));
      reactionBaseline.current = counts;
      momentsInitialized.current = true;
      for (const reaction of LIVE_REACTION_VISUALS) {
        const current = counts[reaction.type] ?? 0;
        if (current > 0) {
          showStageEffect({
            kind: "reaction",
            symbol: reaction.symbol,
            title: reaction.label,
            detail: `+${current} · ${current} total`,
          });
        }
      }
      return;
    }
    for (const event of events) {
      if (seenActivity.current.has(event.id)) continue;
      seenActivity.current.add(event.id);
      const effect = effectForActivity(event);
      if (effect) showStageEffect(effect);
    }
    for (const reaction of LIVE_REACTION_VISUALS) {
      const previous = reactionBaseline.current[reaction.type] ?? 0;
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
    reactionBaseline.current = counts;
  }

  useEffect(() => {
    void api<LiveSettings>("/live/settings")
      .then(setSettings)
      .catch(() => undefined);
    void api<PrivatePeekPolicy>("/live/private-peek-policy")
      .then(setPeekPolicy)
      .catch(() => undefined);
    void refreshPrivateRequests();
    void refreshPrivateSessions();
    void refreshCurrentRoom();
    return () => {
      preflightStream.current?.getTracks().forEach((track) => track.stop());
      providerRoom.current?.disconnect();
      privateProviderRoom.current?.disconnect();
      publisherVideo.current?.replaceChildren();
      for (const timer of momentTimers.current) window.clearTimeout(timer);
      momentTimers.current.clear();
    };
  }, []);

  useEffect(() => {
    seenActivity.current = new Set();
    reactionBaseline.current = {};
    momentsInitialized.current = false;
    setReactionCounts({});
    setStageEffects([]);
    setActivity([]);
    setSupporters([]);
    setGoals([]);
    setAudience(null);
    setSessionSummary({ financial_actions: [] });
    setPresenceNotices([]);
    previousAudience.current = null;
  }, [room?.id]);

  useEffect(() => {
    if (!room?.started_at || room.status !== "live") {
      setElapsedSeconds(0);
      return;
    }
    const update = () => setElapsedSeconds(Math.max(0, Math.floor((Date.now() - new Date(room.started_at!).getTime()) / 1000)));
    update();
    const timer = window.setInterval(update, 1_000);
    return () => window.clearInterval(timer);
  }, [room?.started_at, room?.status]);

  // Publishing succeeds before React has necessarily committed the preview
  // container.  Mount only after that container exists; otherwise a real
  // camera track can be published while the Studio remains on its placeholder.
  useEffect(() => {
    if (!publisherConnected) return;
    try {
      mountCamera();
    } catch (caught) {
      setMessage(
        caught instanceof Error
          ? caught.message
          : "Unable to show your camera preview",
      );
    }
  }, [publisherConnected, room?.id]);

  function disconnectPublisher() {
    providerRoom.current?.disconnect();
    providerRoom.current = null;
    publisherVideo.current?.replaceChildren();
    setPublisherConnected(false);
    setConnectionState("ready");
  }

  function stopPreflight() {
    preflightStream.current?.getTracks().forEach((track) => track.stop());
    preflightStream.current = null;
    if (preflightVideo.current) preflightVideo.current.srcObject = null;
    setPreflightReady(false);
  }

  async function previewDevices() {
    if (!navigator.mediaDevices?.getUserMedia) {
      setPreflightError("Camera preview is not supported by this browser.");
      return;
    }
    stopPreflight();
    setPreflightError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: selectedCamera ? { deviceId: { exact: selectedCamera } } : true,
        audio: selectedMicrophone ? { deviceId: { exact: selectedMicrophone } } : true,
      });
      preflightStream.current = stream;
      if (preflightVideo.current) {
        preflightVideo.current.srcObject = stream;
        await preflightVideo.current.play();
      }
      const devices = await navigator.mediaDevices.enumerateDevices();
      const cameras = devices.filter((device) => device.kind === "videoinput");
      const microphones = devices.filter((device) => device.kind === "audioinput");
      setCameraDevices(cameras);
      setMicrophoneDevices(microphones);
      setSelectedCamera((current) => current || stream.getVideoTracks()[0]?.getSettings().deviceId || cameras[0]?.deviceId || "");
      setSelectedMicrophone((current) => current || stream.getAudioTracks()[0]?.getSettings().deviceId || microphones[0]?.deviceId || "");
      setPreflightReady(true);
    } catch {
      setPreflightError("Camera or microphone access is unavailable. Check browser permissions and try again.");
    }
  }

  function watchConnection(livekitRoom: Room) {
    livekitRoom.on(RoomEvent.Reconnecting, () => setConnectionState("reconnecting"));
    livekitRoom.on(RoomEvent.Reconnected, () => setConnectionState("live"));
  }

  async function publishSelectedDevices(livekitRoom: Room) {
    await Promise.all([
      livekitRoom.localParticipant.setCameraEnabled(
        true,
        selectedCamera ? { deviceId: selectedCamera } : undefined,
      ),
      livekitRoom.localParticipant.setMicrophoneEnabled(
        true,
        selectedMicrophone ? { deviceId: selectedMicrophone } : undefined,
      ),
    ]);
  }

  async function toggleCamera() {
    const livekitRoom = privateProviderRoom.current ?? providerRoom.current;
    if (!livekitRoom) return;
    const next = !cameraEnabled;
    await livekitRoom.localParticipant.setCameraEnabled(next);
    setCameraEnabled(next);
    if (next) mountCamera(livekitRoom);
    else publisherVideo.current?.replaceChildren();
  }

  async function toggleMicrophone() {
    const livekitRoom = privateProviderRoom.current ?? providerRoom.current;
    if (!livekitRoom) return;
    const next = !microphoneEnabled;
    await livekitRoom.localParticipant.setMicrophoneEnabled(next);
    setMicrophoneEnabled(next);
  }

  function requireCamera(livekitRoom: Room) {
    const publication = livekitRoom.localParticipant.getTrackPublication(
      Track.Source.Camera,
    );
    const track = publication?.track;
    if (!track || track.kind !== Track.Kind.Video) {
      throw new Error("Camera track was not published");
    }
    return track;
  }

  function mountCamera(livekitRoom = providerRoom.current) {
    const mount = publisherVideo.current;
    if (!livekitRoom || !mount) return;
    const track = requireCamera(livekitRoom);
    mount.querySelectorAll(":scope > video").forEach((video) => video.remove());
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
      setPrivateRequests(
        await api<PrivateRequest[]>("/live/private-requests/mine/creator"),
      );
    } catch {
      // A viewer does not have a creator queue; keep Studio usable while its
      // own eligibility state is being resolved.
      setPrivateRequests([]);
    }
  }

  async function refreshPrivateSessions() {
    try {
      const sessions = await api<PrivateSession[]>("/live/private-sessions/mine");
      const creatorSession =
        sessions.find((session) => session.participant_role === "creator") ?? null;
      setActivePrivateSession(creatorSession);
      return creatorSession;
    } catch {
      setActivePrivateSession(null);
      return null;
    }
  }

  async function resolvePrivateRequest(
    request: PrivateRequest,
    action: "accept" | "decline",
  ) {
    if (
      action === "accept" &&
      request.mode === "two_to_one" &&
      request.invitation_status !== "accepted"
    ) {
      setMessage(
        `Waiting for ${request.invited_viewer_label ?? "the invited fan"} to accept before this request can proceed.`,
      );
      return;
    }
    try {
      const result = await api<PrivateSession | PrivateRequest>(
        `/live/private-requests/${request.id}/${action}`,
        {
        method: "POST",
        },
      );
      if (action === "accept") setActivePrivateSession(result as PrivateSession);
      setPrivateRequests((current) =>
        current.filter((item) => item.id !== request.id),
      );
      setDismissedPrivateRequests((current) =>
        new Set(current).add(request.id),
      );
      setMessage(
        action === "accept"
          ? "Private session accepted. The public show will pause automatically after payment authorization, then your camera will move into private."
          : "Private session request declined. No payment was initiated.",
      );
    } catch (caught) {
      setMessage(
        caught instanceof ApiError
          ? caught.message
          : "Unable to update private session request",
      );
    }
  }

  async function joinPrivateSession(session: PrivateSession) {
    if (privateJoinInFlight.current || privateProviderRoom.current) return;
    privateJoinInFlight.current = true;
    try {
      await providerRoom.current?.localParticipant.setCameraEnabled(false);
      await providerRoom.current?.localParticipant.setMicrophoneEnabled(false);
      publisherVideo.current
        ?.querySelectorAll(":scope > video")
        .forEach((video) => video.remove());
      const authorization = await api<Token>(
        `/live/private-sessions/${session.id}/token`,
        { method: "POST" },
      );
      const privateRoom = new Room();
      await privateRoom.connect(authorization.provider_url, authorization.token);
      await privateRoom.localParticipant.enableCameraAndMicrophone();
      requireCamera(privateRoom);
      privateProviderRoom.current = privateRoom;
      setPrivateConnected(true);
      mountCamera(privateRoom);
      setMessage(
        "Private session connected. Your public room is paused and will resume when this private ends.",
      );
    } catch (caught) {
      privateProviderRoom.current?.disconnect();
      privateProviderRoom.current = null;
      setPrivateConnected(false);
      await providerRoom.current?.localParticipant.setCameraEnabled(true);
      await providerRoom.current?.localParticipant.setMicrophoneEnabled(true);
      mountCamera();
      setMessage(
        caught instanceof ApiError
          ? caught.message
          : "Unable to move your camera into the private room",
      );
    } finally {
      privateJoinInFlight.current = false;
    }
  }

  async function restorePublicPublisher() {
    privateProviderRoom.current?.disconnect();
    privateProviderRoom.current = null;
    setPrivateConnected(false);
    if (!providerRoom.current) return;
    await providerRoom.current.localParticipant.setCameraEnabled(true);
    await providerRoom.current.localParticipant.setMicrophoneEnabled(true);
    mountCamera();
    setMessage("Private session ended. Your public Live has resumed.");
  }

  async function endActivePrivateSession() {
    if (!activePrivateSession) return;
    try {
      await api(`/live/private-sessions/${activePrivateSession.id}/end`, {
        method: "POST",
      });
      setMessage("Ending private session and resuming your public Live…");
      await refreshPrivateSessions();
    } catch (caught) {
      setMessage(
        caught instanceof ApiError
          ? caught.message
          : "Unable to end the private session",
      );
    }
  }

  useEffect(() => {
    if (!room || room.status !== "live" || !publisherConnected) return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const [messages, events, reactions, pendingPrivateRequests, ranking, audienceSnapshot, commerceSummary, currentGoals] =
          await Promise.all([
            api<Chat[]>(`/live/rooms/${room.id}/chat`),
            api<LiveActivityMoment[]>(`/live/rooms/${room.id}/activity`),
            api<{ counts: Record<string, number> }>(
              `/live/rooms/${room.id}/reactions`,
            ),
            api<PrivateRequest[]>("/live/private-requests/mine/creator"),
            api<Supporter[]>(`/live/rooms/${room.id}/supporters`),
            api<LiveAudience>(`/live/rooms/${room.id}/audience`),
            api<LiveSessionSummary>(`/live/rooms/${room.id}/creator-summary`),
            api<LiveGoal[]>(`/live/rooms/${room.id}/goals`),
          ]);
        if (!cancelled) {
          ingestMoments(events, reactions.counts);
          setChat(messages);
          setActivity(events);
          setReactionCounts(reactions.counts);
          setPrivateRequests(pendingPrivateRequests);
          setSupporters(ranking);
          ingestAudience(audienceSnapshot);
          setSessionSummary(commerceSummary);
          setGoals(currentGoals);
        }
      } catch {
        // Chat delivery is best-effort here. The next bounded refresh will retry
        // without interrupting an otherwise healthy broadcast.
      }
    };
    void refresh();
    const interval = window.setInterval(() => void refresh(), 3_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [room, publisherConnected]);

  useEffect(() => {
    if (!room || room.status !== "live" || !publisherConnected) return;
    let cancelled = false;
    let nextRefresh: number | undefined;
    const refresh = async () => {
      await refreshPrivateSessions();
      if (!cancelled)
        nextRefresh = window.setTimeout(() => void refresh(), 2_000);
    };
    void refresh();
    return () => {
      cancelled = true;
      if (nextRefresh !== undefined) window.clearTimeout(nextRefresh);
    };
  }, [room?.id, room?.status, publisherConnected]);

  useEffect(() => {
    if (
      !activePrivateSession ||
      !publisherConnected ||
      privateConnected ||
      !["ready", "connecting", "active", "reconnecting"].includes(
        activePrivateSession.status,
      )
    )
      return;
    void joinPrivateSession(activePrivateSession);
  }, [activePrivateSession, privateConnected, publisherConnected]);

  useEffect(() => {
    if (activePrivateSession || !privateConnected) return;
    void restorePublicPublisher();
  }, [activePrivateSession, privateConnected]);

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
          setMessage(
            "Live room ended. You can now accept queued private requests.",
          );
          await refreshPrivateRequests();
          return;
        }
      } catch {
        // Provider termination is durable and retryable. Keep the deny-first
        // state visible and retry this bounded status read without overlapping
        // requests or weakening room authority.
      }
      if (!cancelled)
        nextRefresh = window.setTimeout(() => void refreshEndingRoom(), 1_000);
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
          private_sessions_enabled:
            values.get("private-sessions-enabled") === "on",
          one_to_one_price_minor: Math.round(Number(values.get("one-to-one-price")) * 100),
          two_to_one_price_minor: Math.round(Number(values.get("two-to-one-price")) * 100),
          currency: values.get("private-currency"),
          minimum_minutes: Number(values.get("private-minimum-minutes")),
          max_authorization_minor: Math.round(Number(values.get("private-max-authorization")) * 100),
          private_peeks_enabled: values.get("private-peeks-enabled") === "on",
        }),
      });
      setSettings(updated);
      setMessage(
        "Private-session pricing saved. It applies to future requests only.",
      );
    } catch (caught) {
      setMessage(
        caught instanceof ApiError
          ? caught.message
          : "Unable to save private-session pricing",
      );
    }
  }

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    if (liveActionInFlight.current) return;
    liveActionInFlight.current = true;
    setLiveActionPending(true);
    try {
      stopPreflight();
      const started = await api<LiveRoom>("/live/rooms", {
        method: "POST",
        body: JSON.stringify({
          title: values.get("title"),
          access_mode: values.get("access_mode"),
        }),
      });
      const authorization = await api<Token>(
        `/live/rooms/${started.id}/token`,
        { method: "POST" },
      );
      const livekitRoom = new Room();
      setConnectionState("connecting");
      watchConnection(livekitRoom);
      await livekitRoom.connect(
        authorization.provider_url,
        authorization.token,
      );
      await publishSelectedDevices(livekitRoom);
      if (livekitRoom.localParticipant.trackPublications.size < 2) {
        throw new Error("Camera and microphone tracks were not published");
      }
      requireCamera(livekitRoom);
      providerRoom.current = livekitRoom;
      setRoom(started);
      setPublisherConnected(true);
      setConnectionState("live");
      setCameraEnabled(true);
      setMicrophoneEnabled(true);
      await refreshChat(started);
      setMessage(
        "You are live. Your camera, microphone, and creator chat are ready.",
      );
    } catch (caught) {
      disconnectPublisher();
      if (
        caught instanceof ApiError &&
        caught.message === "Creator already has an active public live room"
      ) {
        await refreshCurrentRoom();
        setMessage(
          "Your public room is already live. Rejoin it or end it before starting another.",
        );
        return;
      }
      setMessage(
        caught instanceof ApiError
          ? caught.message
          : "Unable to start live room",
      );
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
      const authorization = await api<Token>(`/live/rooms/${room.id}/token`, {
        method: "POST",
      });
      const livekitRoom = new Room();
      setConnectionState("connecting");
      watchConnection(livekitRoom);
      await livekitRoom.connect(
        authorization.provider_url,
        authorization.token,
      );
      await publishSelectedDevices(livekitRoom);
      if (livekitRoom.localParticipant.trackPublications.size < 2) {
        throw new Error("Camera and microphone tracks were not published");
      }
      requireCamera(livekitRoom);
      providerRoom.current = livekitRoom;
      setPublisherConnected(true);
      setConnectionState("live");
      setCameraEnabled(true);
      setMicrophoneEnabled(true);
      await refreshChat(room);
      setMessage(
        "You are live again. Your camera, microphone, and creator chat are ready.",
      );
    } catch (caught) {
      disconnectPublisher();
      setMessage(
        caught instanceof ApiError
          ? caught.message
          : "Unable to rejoin live room",
      );
    } finally {
      liveActionInFlight.current = false;
      setLiveActionPending(false);
    }
  }

  async function end() {
    if (!room) return;
    if (liveActionInFlight.current) return;
    liveActionInFlight.current = true;
    setLiveActionPending(true);
    try {
      const ended = await api<LiveRoom>(`/live/rooms/${room.id}/end`, {
        method: "POST",
      });
      setLastSessionSummary({ duration: elapsedSeconds, viewers: audience?.current_viewers ?? room.viewer_count, peak: audience?.peak_viewers ?? room.peak_viewer_count, unique: audience?.unique_viewers ?? 0, tips: sessionCommerce.tips, gifts: sessionCommerce.gifts, requests: sessionCommerce.requests, snapshots: sessionCommerce.snapshots });
      setEndConfirmOpen(false);
      disconnectPublisher();
      setRoom(ended);
      setMessage("Ending live for everyone…");
    } catch (caught) {
      setMessage(
        caught instanceof ApiError ? caught.message : "Unable to end live room",
      );
    } finally {
      liveActionInFlight.current = false;
      setLiveActionPending(false);
    }
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
      setChat((current) =>
        current.some((item) => item.id === posted.id)
          ? current
          : [...current, posted],
      );
    } catch (caught) {
      setMessage(
        caught instanceof ApiError ? caught.message : "Unable to send chat",
      );
    }
  }

  const livePrivateRequest =
    room?.status === "live"
      ? (privateRequests.find(
          (request) => !dismissedPrivateRequests.has(request.id),
        ) ?? null)
      : null;

  return (
    <section className="card" aria-label="Live Studio">
      <p className="eyebrow">LIVE STUDIO</p>
      <h2>Go live</h2>
      {!roomChecked ? (
        <p>Checking your current live room…</p>
      ) : !room || room.status === "ended" ? (
        <div className={styles.creatorPreLive}>
          {lastSessionSummary && (
            <section aria-label="Previous Live summary" className={styles.creatorPostLiveSummary}>
              <header><span>SHOW COMPLETE</span><strong>Your last Live at a glance</strong></header>
              <div>
                <span><b>{formatDuration(lastSessionSummary.duration)}</b> duration</span>
                <span><b>{lastSessionSummary.peak}</b> peak viewers</span>
                <span><b>{lastSessionSummary.unique}</b> unique viewers</span>
                <span><b>{lastSessionSummary.tips + lastSessionSummary.gifts}</b> tips &amp; gifts</span>
                <span><b>{lastSessionSummary.requests}</b> paid requests</span>
                <span><b>{lastSessionSummary.snapshots}</b> snapshots</span>
              </div>
            </section>
          )}
          <form aria-label="Pre-live setup" className={styles.creatorPreLiveForm} onSubmit={start}>
            <header><span>READY TO GO LIVE</span><strong>Set the scene</strong><p>Your camera and microphone are checked securely when you start.</p></header>
            <div className={styles.creatorPreflightPreview}>
              <video aria-label="Camera preview" autoPlay muted playsInline ref={preflightVideo} />
              {!preflightReady && <span>Preview your framing before viewers arrive.</span>}
              <button onClick={() => void previewDevices()} type="button">{preflightReady ? "Refresh preview" : "Preview camera & mic"}</button>
            </div>
            {preflightError && <p className={styles.creatorPreflightError} role="alert">{preflightError}</p>}
            {(cameraDevices.length > 0 || microphoneDevices.length > 0) && (
              <div className={styles.creatorDeviceGrid}>
                <label>Camera<select onChange={(event) => setSelectedCamera(event.target.value)} value={selectedCamera}>{cameraDevices.map((device, index) => <option key={device.deviceId} value={device.deviceId}>{device.label || `Camera ${index + 1}`}</option>)}</select></label>
                <label>Microphone<select onChange={(event) => setSelectedMicrophone(event.target.value)} value={selectedMicrophone}>{microphoneDevices.map((device, index) => <option key={device.deviceId} value={device.deviceId}>{device.label || `Microphone ${index + 1}`}</option>)}</select></label>
              </div>
            )}
            <label>
              Live title
              <input name="title" maxLength={160} placeholder="What are you sharing today?" required />
            </label>
            <label>
              Audience
              <select name="access_mode" defaultValue="public">
                <option value="public">Public</option>
                <option value="followers">Followers</option>
                <option value="subscribers">Subscribers</option>
              </select>
            </label>
            <div className={styles.creatorPreLiveChecks}><span>Camera <b>checked on start</b></span><span>Microphone <b>checked on start</b></span><span>Private sessions <b>{settings?.private_sessions_enabled ? "enabled" : "off"}</b></span></div>
            <button disabled={liveActionPending}>
              {liveActionPending ? "Connecting camera and mic…" : "Start live"}
            </button>
          </form>
        </div>
      ) : (
        <>
          {room.status === "ending" ? (
            <div className={styles.creatorLiveEnding}>
              <p className="eyebrow">ENDING LIVE</p>
              <h3>{room.title}</h3>
              <p>Your room is closing for everyone. A new public room can start as soon as provider closure is confirmed.</p>
            </div>
          ) : (
            <>
              {publisherConnected && (
                <section
                  className={`${styles.liveViewer} ${styles.liveCreatorViewer}`}
                  aria-label="Your live broadcast"
                >
                  <div
                    aria-label="Your live camera preview"
                    className={styles.liveStage}
                  >
                    <div aria-hidden="true" className={styles.liveMediaMount} ref={publisherVideo} />
                    <div className={styles.liveStagePlaceholder}>
                      <span aria-hidden="true">●</span>
                      <p>Starting your camera…</p>
                    </div>
                    <div className={styles.liveStageHeader}>
                      <span className={styles.liveBadge}>
                        {privateConnected ? "PRIVATE" : "LIVE"}
                      </span>
                      <strong>{privateConnected ? "Private session" : room.title}</strong>
                    </div>
                    <div aria-label="Broadcast status" className={styles.creatorBroadcastStatus}>
                      <span className={connectionState === "reconnecting" ? styles.liveConnectionWarning : ""}><i aria-hidden="true" />{connectionState === "reconnecting" ? "Reconnecting…" : "Connected"}</span>
                      <b>{formatDuration(elapsedSeconds)}</b>
                      <span>{audience?.current_viewers ?? room.viewer_count} watching</span>
                    </div>
                    <div aria-label="Broadcast controls" className={styles.creatorBroadcastControls} role="group">
                      <button aria-pressed={!microphoneEnabled} onClick={() => void toggleMicrophone()} type="button">{microphoneEnabled ? "Mic on" : "Mic off"}</button>
                      <button aria-pressed={!cameraEnabled} onClick={() => void toggleCamera()} type="button">{cameraEnabled ? "Camera on" : "Camera off"}</button>
                    </div>
                    {!privateConnected && (
                      <button
                        className={styles.creatorEndLive}
                        disabled={liveActionPending}
                        onClick={() => setEndConfirmOpen(true)}
                        type="button"
                      >
                        End public live
                      </button>
                    )}
                    {activePrivateSession && (
                      <section
                        aria-label="Active private session"
                        aria-live="polite"
                        className={styles.creatorPrivateSessionStatus}
                      >
                        <strong>
                          {privateConnected
                            ? "You are in private"
                            : "Private accepted — waiting for payment"}
                        </strong>
                        <span>
                          {activePrivateSession.peeks_allowed
                            ? `Paid view-only peeks are allowed at ${formatMoney(
                                activePrivateSession.peek_price_minor ?? 0,
                                activePrivateSession.peek_currency ?? "EUR",
                              )} per viewer with ${
                                (activePrivateSession.peek_commission_basis_points ?? 0) /
                                100
                              }% platform commission.`
                            : "Peeks are disabled for this session."}
                        </span>
                        {privateConnected && (
                          <button
                            onClick={() => void endActivePrivateSession()}
                            type="button"
                          >
                            End private and resume public
                          </button>
                        )}
                      </section>
                    )}
                    {livePrivateRequest && (
                      <section
                        aria-label="New private session request"
                        aria-live="assertive"
                        className={styles.creatorLiveRequestAlert}
                        role="alertdialog"
                      >
                        <p className="eyebrow">PRIVATE REQUEST</p>
                        <h3>
                          {livePrivateRequest.mode === "two_to_one"
                            ? "2-to-1 session requested"
                            : "Private 1:1 requested"}
                        </h3>
                        <p>
                          {formatMoney(
                            livePrivateRequest.per_minute_price_minor,
                            livePrivateRequest.currency,
                          )}
                          /minute · minimum{" "}
                          {formatMoney(
                            livePrivateRequest.minimum_charge_minor,
                            livePrivateRequest.currency,
                          )}
                        </p>
                        <small>
                          Accepting does not end this public show. After the fan
                          authorizes payment, the public video pauses and your
                          camera moves into private automatically.
                        </small>
                        <div>
                          <button
                            onClick={() =>
                              setDismissedPrivateRequests((current) =>
                                new Set(current).add(livePrivateRequest.id),
                              )
                            }
                            type="button"
                          >
                            Keep queued
                          </button>
                          <button
                            onClick={() =>
                              void resolvePrivateRequest(
                                livePrivateRequest,
                                "decline",
                              )
                            }
                            type="button"
                          >
                            Decline
                          </button>
                          <button
                            onClick={() =>
                              void resolvePrivateRequest(
                                livePrivateRequest,
                                "accept",
                              )
                            }
                            type="button"
                          >
                            Accept private
                          </button>
                        </div>
                      </section>
                    )}
                    <LiveStageMoments
                      effects={stageEffects}
                      reactionCounts={reactionCounts}
                    />
                  </div>
                  <section aria-label="Current Live summary" className={styles.creatorLiveSummary}>
                    <header>
                      <div><span>SESSION NOW</span><strong>Live performance</strong></div>
                      <a href="/creator-studio/analytics">Historical analytics</a>
                    </header>
                    <div className={styles.creatorLiveKpis}>
                      <article><span>Watching</span><strong>{audience?.current_viewers ?? room.viewer_count}</strong></article>
                      <article><span>Peak</span><strong>{audience?.peak_viewers ?? room.peak_viewer_count}</strong></article>
                      <article><span>Unique viewers</span><strong>{audience?.unique_viewers ?? 0}</strong></article>
                      <article><span>Chat messages</span><strong>{chat.length}</strong></article>
                      <article><span>Reactions</span><strong>{Object.values(reactionCounts).reduce((sum, count) => sum + count, 0)}</strong></article>
                      <article><span>Tips / gifts</span><strong>{sessionCommerce.tips} / {sessionCommerce.gifts}</strong></article>
                      <article><span>Paid requests</span><strong>{sessionCommerce.requests}</strong></article>
                      <article><span>Snapshots</span><strong>{sessionCommerce.snapshots}</strong></article>
                    </div>
                    {goals.map((goal) => (
                      <section aria-label={`Active goal: ${goal.title}`} className={styles.creatorActiveGoal} key={goal.id}>
                        <div><span>ACTIVE GOAL</span><strong>{goal.title}</strong></div>
                        <div><b>{formatMoney(goal.progress_amount_minor, goal.currency)} / {formatMoney(goal.target_amount_minor, goal.currency)}</b><progress max={goal.target_amount_minor} value={Math.min(goal.progress_amount_minor, goal.target_amount_minor)} /></div>
                      </section>
                    ))}
                    <div className={styles.creatorLiveSummaryDetails}>
                      <section><strong>Session value</strong>{sessionCommerce.totals.length ? sessionCommerce.totals.map(([currency, amount]) => <span key={currency}>{formatMoney(amount, currency)}</span>) : <span>No confirmed payments yet</span>}</section>
                      <section><strong>Top supporters</strong>{supporters.length ? supporters.slice(0, 5).map((supporter) => <span key={`${supporter.rank}-${supporter.supporter_label}`}>{supporter.rank}. {supporter.supporter_label} · {formatMoney(supporter.amount_minor, supporter.currency)}</span>) : <span>No supporters yet</span>}</section>
                      <section><strong>Audience now</strong>{audience?.members.length ? audience.members.slice(0, 8).map((member) => <span key={member.user_id}>{member.label}</span>) : <span>No viewers connected</span>}</section>
                      <section aria-live="polite"><strong>Presence</strong>{presenceNotices.length ? presenceNotices.map((notice, index) => <span key={`${notice}-${index}`}>{notice}</span>) : <span>Join and leave activity will appear here</span>}</section>
                    </div>
                  </section>
                  <aside
                    className={`${styles.liveChat} ${styles.liveChatCreator}`}
                  >
                    <h2>Creator chat <span>{audience?.current_viewers ?? room.viewer_count} watching</span></h2>
                    <div aria-label="Creator Live panels" className={styles.liveRailTabs} role="tablist">
                      {(["chat", "activity"] as const).map((tab) => <button aria-selected={studioRailTab === tab} key={tab} onClick={() => setStudioRailTab(tab)} role="tab" type="button">{tab}</button>)}
                    </div>
                    <div className={styles.liveChatMessages} hidden={studioRailTab !== "chat"}>
                      {chat.length ? (
                        chat.map((item) => <p key={item.id}><strong>{item.sender_label}</strong><span>{item.body}</span></p>)
                      ) : (
                        <p>Say hello to everyone watching.</p>
                      )}
                    </div>
                    <div className={styles.liveActivityTicker} hidden={studioRailTab !== "activity"}>
                      {presenceNotices.map((notice, index) => <span key={`${notice}-${index}`}><strong>Audience</strong>{notice}</span>)}
                      {activity.length ? activity.slice(-20).reverse().map((item) => { const copy = describeActivity(item); return <span key={item.id}><strong>{copy.title}</strong>{copy.detail}</span>; }) : <p className={styles.liveRailEmpty}>Tips, gifts, requests and goal progress will appear here.</p>}
                    </div>
                    <form hidden={studioRailTab !== "chat"} onSubmit={sendChat}>
                      <label className="sr-only" htmlFor="creator-live-chat">
                        Live chat message
                      </label>
                      <input
                        id="creator-live-chat"
                        maxLength={1000}
                        name="body"
                        placeholder="Chat with your audience…"
                        required
                      />
                      <button type="submit">Send</button>
                    </form>
                  </aside>
                </section>
              )}
              {!publisherConnected && (
                <div className={styles.creatorReconnectPanel}>
                  <strong>{room.title}</strong>
                  <p>Your public room is active, but this browser is no longer publishing.</p>
                  <div>
                    <button disabled={liveActionPending} onClick={() => void rejoin()}>
                      {liveActionPending ? "Joining live…" : "Rejoin camera and mic"}
                    </button>
                    <button disabled={liveActionPending} onClick={() => setEndConfirmOpen(true)}>
                      End public live
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}
      {endConfirmOpen && room && room.status !== "ended" && (
        <div className={styles.creatorEndConfirmLayer} onMouseDown={() => setEndConfirmOpen(false)}>
          <section aria-label="Confirm end Live" aria-modal="true" onMouseDown={(event) => event.stopPropagation()} role="dialog">
            <span>END LIVE?</span><strong>Finish this broadcast for everyone</strong><p>Viewers will see your offline cover and you’ll receive a trustworthy session summary.</p>
            <div><button onClick={() => setEndConfirmOpen(false)} type="button">Keep streaming</button><button onClick={() => void end()} type="button">End Live now</button></div>
          </section>
        </div>
      )}
      {settings && (
        <>
          <button className={styles.creatorSetupShortcut} onClick={() => setPrivateSettingsOpen(true)} type="button">
            <span><strong>Private sessions</strong><small>Pricing, availability and paid-peek preference</small></span>
            <b>{settings.private_sessions_enabled ? "Enabled" : "Off"}</b>
          </button>
          {privateSettingsOpen && (
            <div className={styles.creatorSetupModalLayer} onMouseDown={() => setPrivateSettingsOpen(false)}>
              <form
                aria-label="Private session pricing"
                aria-modal="true"
                className={styles.creatorSetupPanel}
                onMouseDown={(event) => event.stopPropagation()}
                onSubmit={async (event) => {
                  await savePrivatePricing(event);
                  setPrivateSettingsOpen(false);
                }}
                role="dialog"
              >
                <header>
                  <div><p className="eyebrow">LIVE SETUP</p><h2>Private sessions</h2></div>
                  <button aria-label="Close private session settings" onClick={() => setPrivateSettingsOpen(false)} type="button">×</button>
                </header>
          <label>
            <input
              name="private-sessions-enabled"
              type="checkbox"
              defaultChecked={settings.private_sessions_enabled}
            />{" "}
            Enable private sessions
          </label>
          <label>
            <input
              name="private-peeks-enabled"
              type="checkbox"
              defaultChecked={settings.private_peeks_enabled}
            />{" "}
            Allow paid, view-only peeks during future private sessions
          </label>
          <p>
            FanBackstage administrators set the peek price and commission. Your
            choice is snapshotted when you accept a private request.
          </p>
          {peekPolicy && (
            <p>
              Current terms: {formatMoney(peekPolicy.amount_minor, peekPolicy.currency)}
              {" per viewer · "}
              {peekPolicy.commission_basis_points / 100}% platform commission ·{" "}
              {formatMoney(
                Math.floor(
                  (peekPolicy.amount_minor *
                    (10_000 - peekPolicy.commission_basis_points)) /
                    10_000,
                ),
                peekPolicy.currency,
              )}{" "}
              creator pool before any accepted group split.
            </p>
          )}
          <label>
            1:1 price per minute ({settings.currency})
            <input
              name="one-to-one-price"
              type="number"
              min="0.01"
              step="0.01"
              defaultValue={(settings.one_to_one_price_minor / 100).toFixed(2)}
              required
            />
          </label>
          <label>
            2-to-1 price per minute ({settings.currency})
            <input
              name="two-to-one-price"
              type="number"
              min="0.01"
              step="0.01"
              defaultValue={(settings.two_to_one_price_minor / 100).toFixed(2)}
              required
            />
          </label>
          <label>
            Currency
            <input
              name="private-currency"
              defaultValue={settings.currency}
              minLength={3}
              maxLength={3}
              required
            />
          </label>
          <label>
            Minimum minutes
            <input
              name="private-minimum-minutes"
              type="number"
              min="1"
              defaultValue={settings.minimum_minutes}
              required
            />
          </label>
          <label>
            Maximum payment authorization ({settings.currency})
            <input
              name="private-max-authorization"
              type="number"
              min="0.01"
              step="0.01"
              defaultValue={(settings.max_authorization_minor / 100).toFixed(2)}
              required
            />
          </label>
          <button>Save private-session pricing</button>
              </form>
            </div>
          )}
        </>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
