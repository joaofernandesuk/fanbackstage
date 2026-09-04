"use client";

import Image from "next/image";

import { formatMoney } from "../lib/public-api";
import styles from "./social-surface.module.css";

export type LiveActivityMoment = {
  id: string;
  event_type: string;
  amount_minor: number | null;
  currency: string | null;
  metadata: Record<string, string>;
};

export type LiveStageEffect = {
  id: string;
  kind: "reaction" | "tip" | "gift" | "request" | "goal" | "snapshot" | "vip";
  symbol?: string;
  icon?: string;
  title: string;
  detail: string;
};

export const LIVE_REACTION_VISUALS = [
  { type: "love", symbol: "♥", label: "Love" },
  { type: "fire", symbol: "🔥", label: "Fire" },
  { type: "applause", symbol: "👏", label: "Applause" },
  { type: "wow", symbol: "✨", label: "Wow" },
] as const;

export function effectForActivity(event: LiveActivityMoment): Omit<LiveStageEffect, "id"> | null {
  const amount = event.amount_minor && event.currency
    ? formatMoney(event.amount_minor, event.currency)
    : "";
  if (event.event_type === "tip") {
    return {
      kind: "tip",
      icon: event.metadata.tip_icon,
      symbol: "♥",
      title: event.metadata.tip_label || event.metadata.tip_menu_label || "A fan sent a tip",
      detail: amount,
    };
  }
  if (event.event_type === "gift") {
    return {
      kind: "gift",
      icon: event.metadata.gift_icon,
      symbol: "🎁",
      title: event.metadata.gift_name || "A fan sent a gift",
      detail: amount,
    };
  }
  if (event.event_type === "snapshot") {
    return {
      kind: "snapshot",
      symbol: "📸",
      title: event.metadata.snapshot_label || "A fan captured a snapshot",
      detail: amount,
    };
  }
  if (event.event_type === "vip_admission") {
    return {
      kind: "vip",
      symbol: "♛",
      title: "A fan joined the VIP show",
      detail: amount,
    };
  }
  if (event.event_type === "vip_started") {
    return {
      kind: "vip",
      symbol: "♛",
      title: event.metadata.title || "VIP show started",
      detail: "VIP show live",
    };
  }
  if (event.event_type === "paid_request_pending") {
    return {
      kind: "request",
      symbol: "✦",
      title: event.metadata.request_label || "Paid request sent",
      detail: "Waiting for the creator",
    };
  }
  if (event.event_type === "paid_request") {
    return {
      kind: "request",
      symbol: "✓",
      title: event.metadata.request_label || "Paid request accepted",
      detail: amount,
    };
  }
  if (event.event_type === "goal_completed") {
    return {
      kind: "goal",
      symbol: "★",
      title: event.metadata.title || "Live goal completed",
      detail: "Goal complete",
    };
  }
  return null;
}

export function LiveStageMoments({
  effects,
  reactionCounts,
}: {
  effects: LiveStageEffect[];
  reactionCounts: Record<string, number>;
}) {
  const total = Object.values(reactionCounts).reduce((sum, count) => sum + count, 0);
  return (
    <>
      <div aria-atomic="false" aria-live="polite" className={styles.liveMomentLayer}>
        {effects.map((effect) => (
          <article
            aria-label={`${effect.title}${effect.detail ? ` ${effect.detail}` : ""}`}
            className={`${styles.liveMoment} ${styles[`liveMoment${effect.kind[0].toUpperCase()}${effect.kind.slice(1)}`]}`}
            key={effect.id}
          >
            <span aria-hidden="true" className={styles.liveMomentVisual}>
              {effect.icon ? <Image alt="" height={72} src={effect.icon} width={72} /> : effect.symbol}
            </span>
            <span>
              <strong>{effect.title}</strong>
              {effect.detail && <small>{effect.detail}</small>}
            </span>
          </article>
        ))}
      </div>
      <div aria-label={`${total} live reactions`} className={styles.liveReactionTotals}>
        {LIVE_REACTION_VISUALS.map((reaction) => (
          <span key={reaction.type} title={`${reaction.label}: ${reactionCounts[reaction.type] ?? 0}`}>
            <b aria-hidden="true">{reaction.symbol}</b>
            <small>{reactionCounts[reaction.type] ?? 0}</small>
          </span>
        ))}
      </div>
    </>
  );
}
