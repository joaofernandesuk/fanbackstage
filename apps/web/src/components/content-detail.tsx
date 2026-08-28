"use client";

import Link from "next/link";
import {
  TouchEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { api, ApiError } from "../lib/api";
import {
  ContentMedia,
  contentDeliveryUrl,
  contentDetailPath,
  getPublicContent,
  getPublicCreatorContent,
  PublicContent,
} from "../lib/content-api";
import { formatMoney } from "../lib/public-api";
import { AdultAccessGate } from "./adult-access-gate";
import { AccessBadge, CreatorAvatar, EmptyState, Skeleton, useLoginGate } from "./consumer-ui";
import { SubscriptionOptions } from "./subscription-options";
import styles from "./content-detail.module.css";

function durationLabel(seconds: number | null) {
  if (!seconds) return null;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes}:${String(remainder).padStart(2, "0")}` : `0:${String(remainder).padStart(2, "0")}`;
}

export function relatedCreatorContent(
  contentId: string,
  items: readonly PublicContent[],
  limit = 4,
): PublicContent[] {
  return items.filter((item) => item.id !== contentId).slice(0, Math.max(0, limit));
}

export function purchasePaymentRequiresNewKey(status: string): boolean {
  return status === "failed";
}

export function purchaseAttemptKey(
  current: string | null,
  createKey: () => string = () => crypto.randomUUID(),
): string {
  return current ?? createKey();
}

function PurchaseDialog({
  content,
  open,
  working,
  error,
  onClose,
  onConfirm,
}: {
  content: PublicContent;
  open: boolean;
  working: boolean;
  error: string;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (open && !node.open) node.showModal();
    if (!open && node.open) node.close();
  }, [open]);

  const price = content.price_amount_minor != null && content.price_currency
    ? formatMoney(content.price_amount_minor, content.price_currency)
    : "the displayed price";

  return (
    <dialog
      aria-labelledby="purchase-title"
      className={styles.purchaseDialog}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
      ref={dialog}
    >
      <div className={styles.purchasePanel}>
        <button aria-label="Close purchase confirmation" className={styles.closeButton} onClick={onClose} type="button">×</button>
        <p className={styles.eyebrow}>CONFIRM UNLOCK</p>
        <h2 id="purchase-title">Unlock {content.title}</h2>
        <p>By {content.creator_display_name ?? "FanBackstage creator"}</p>
        <div className={styles.purchaseTotal}>
          <span>Total</span>
          <strong>{price}</strong>
        </div>
        <p className={styles.providerNote}>The configured payment provider confirms this exact external charge before access is granted. This purchase does not use a wallet or credits balance.</p>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <div className={styles.dialogActions}>
          <button className={styles.secondaryButton} disabled={working} onClick={onClose} type="button">Cancel</button>
          <button className={styles.primaryButton} disabled={working} onClick={onConfirm} type="button">{working ? "Confirming…" : `Confirm ${price}`}</button>
        </div>
      </div>
    </dialog>
  );
}

function GalleryViewer({ content }: { content: PublicContent }) {
  const items = content.has_access ? content.media : content.previews;
  const [selected, setSelected] = useState(0);
  const [lightbox, setLightbox] = useState(false);
  const [touchStart, setTouchStart] = useState<number | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const current = items[Math.min(selected, Math.max(0, items.length - 1))];

  const move = useCallback((direction: number) => {
    setSelected((value) => {
      if (!items.length) return 0;
      return (value + direction + items.length) % items.length;
    });
  }, [items.length]);

  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (lightbox && !node.open) node.showModal();
    if (!lightbox && node.open) node.close();
  }, [lightbox]);

  useEffect(() => {
    if (!lightbox) return;
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight") move(1);
      if (event.key === "ArrowLeft") move(-1);
    };
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  }, [lightbox, move]);

  function finishSwipe(event: TouchEvent) {
    if (touchStart == null) return;
    const distance = event.changedTouches[0]?.clientX - touchStart;
    if (Math.abs(distance) > 45) move(distance < 0 ? 1 : -1);
    setTouchStart(null);
  }

  const lockedRemainder = Math.max(0, content.media_count - items.length);
  return (
    <section aria-label={`${content.title} gallery`} className={styles.galleryViewer}>
      <div
        className={`${styles.galleryStage} ${content.locked ? styles.lockedStage : ""}`}
        onTouchEnd={finishSwipe}
        onTouchStart={(event) => setTouchStart(event.touches[0]?.clientX ?? null)}
      >
        {current ? (
          <button aria-label={`Open ${content.title} image ${selected + 1} full screen`} className={styles.stageImageButton} disabled={!content.has_access} onClick={() => setLightbox(true)} type="button">
            <img alt={`${content.title} image ${selected + 1}`} src={contentDeliveryUrl(current.delivery_path)} />
          </button>
        ) : <div className={styles.mediaFallback}>Preview unavailable</div>}
        {items.length > 1 && (
          <>
            <button aria-label="Previous image" className={`${styles.galleryArrow} ${styles.previous}`} onClick={() => move(-1)} type="button">‹</button>
            <button aria-label="Next image" className={`${styles.galleryArrow} ${styles.next}`} onClick={() => move(1)} type="button">›</button>
          </>
        )}
        <span className={styles.imageCounter}>{items.length ? selected + 1 : 0} / {content.media_count}</span>
      </div>
      {items.length > 1 && (
        <div aria-label="Gallery thumbnails" className={styles.thumbnails} role="group">
          {items.map((item, index) => (
            <button aria-label={`View image ${index + 1}`} aria-pressed={selected === index} key={item.derivative_id} onClick={() => setSelected(index)} type="button">
              <img alt="" src={contentDeliveryUrl(item.delivery_path)} />
            </button>
          ))}
        </div>
      )}
      {content.locked && lockedRemainder > 0 && (
        <div className={styles.lockedRemainder}>
          {Array.from({ length: Math.min(lockedRemainder, 4) }, (_, index) => <span aria-hidden="true" key={index}>🔒</span>)}
          <strong>{lockedRemainder} more photo{lockedRemainder === 1 ? "" : "s"} unlock with access</strong>
        </div>
      )}
      <dialog
        aria-label={`${content.title} full-screen gallery`}
        className={styles.lightbox}
        onCancel={(event) => { event.preventDefault(); setLightbox(false); }}
        onClose={() => setLightbox(false)}
        ref={dialog}
      >
        <button aria-label="Close full-screen gallery" className={styles.lightboxClose} onClick={() => setLightbox(false)} type="button">×</button>
        {current && <img alt={`${content.title} full-screen image ${selected + 1}`} src={contentDeliveryUrl(current.delivery_path)} />}
        {items.length > 1 && <><button aria-label="Previous image" className={`${styles.galleryArrow} ${styles.previous}`} onClick={() => move(-1)} type="button">‹</button><button aria-label="Next image" className={`${styles.galleryArrow} ${styles.next}`} onClick={() => move(1)} type="button">›</button></>}
      </dialog>
    </section>
  );
}

function RelatedContent({ current, items }: { current: PublicContent; items: PublicContent[] }) {
  if (!items.length) return null;
  const creatorName = current.creator_display_name ?? "this creator";
  return (
    <section aria-labelledby="related-content-heading" className={styles.relatedSection}>
      <div className={styles.relatedHeader}>
        <div>
          <p className={styles.eyebrow}>KEEP EXPLORING</p>
          <h2 id="related-content-heading">More from {creatorName}</h2>
        </div>
        {current.creator_username && (
          <Link href={`/creator/${encodeURIComponent(current.creator_username)}`}>View creator profile</Link>
        )}
      </div>
      <div className={styles.relatedGrid}>
        {items.map((item) => {
          const preview = item.previews.find((media) => media.kind !== "trailer") ?? item.previews[0];
          return (
            <Link className={styles.relatedCard} href={contentDetailPath(item.id)} key={item.id}>
              <span className={styles.relatedMedia}>
                {preview ? (
                  <img alt={`${item.title} preview`} src={contentDeliveryUrl(preview.delivery_path)} />
                ) : (
                  <span className={styles.relatedFallback}>Preview unavailable</span>
                )}
                <AccessBadge locked={item.locked} policy={item.access_policy} />
              </span>
              <span className={styles.relatedBody}>
                <strong>{item.title}</strong>
                <small>
                  {item.content_type === "gallery"
                    ? `${item.media_count} photo${item.media_count === 1 ? "" : "s"}`
                    : durationLabel(item.duration_seconds) ?? "Video"}
                </small>
              </span>
            </Link>
          );
        })}
      </div>
    </section>
  );
}

function VideoViewer({ content }: { content: PublicContent }) {
  const poster = content.previews.find((item) => item.kind === "poster");
  const trailer = content.previews.find((item) => item.kind === "trailer");
  const playback = content.media.find((item) => item.kind === "playback");
  if (content.has_access && playback) {
    return (
      <div className={styles.videoFrame}>
        <video controls playsInline poster={poster ? contentDeliveryUrl(poster.delivery_path) : undefined} preload="metadata" src={contentDeliveryUrl(playback.delivery_path)} />
        <span className={styles.videoLabel}>FULL VIDEO</span>
      </div>
    );
  }
  if (trailer) {
    return (
      <div className={styles.videoFrame}>
        <video aria-label={`${content.title} preview trailer`} controls playsInline poster={poster ? contentDeliveryUrl(poster.delivery_path) : undefined} preload="metadata" src={contentDeliveryUrl(trailer.delivery_path)} />
        <span className={styles.videoLabel}>PREVIEW TRAILER · {durationLabel(trailer.duration_seconds)}</span>
      </div>
    );
  }
  return <div className={styles.videoFrame}>{poster ? <img alt={`${content.title} poster`} src={contentDeliveryUrl(poster.delivery_path)} /> : <div className={styles.mediaFallback}>Preview unavailable</div>}</div>;
}

export function ContentDetail({ contentId }: { contentId: string }) {
  const { authenticated, loading: authLoading, requireLogin } = useLoginGate();
  const [content, setContent] = useState<PublicContent | null>(null);
  const [related, setRelated] = useState<PublicContent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [purchaseOpen, setPurchaseOpen] = useState(false);
  const [purchasing, setPurchasing] = useState(false);
  const [purchaseError, setPurchaseError] = useState("");
  const [purchaseStatus, setPurchaseStatus] = useState("");
  const idempotencyKey = useRef<string | null>(null);

  const load = useCallback(async () => {
    setError("");
    try {
      const resolved = await getPublicContent(contentId);
      setContent(resolved);
      if (resolved.creator_username) {
        try {
          const creatorItems = await getPublicCreatorContent(resolved.creator_username);
          setRelated(relatedCreatorContent(resolved.id, creatorItems));
        } catch {
          setRelated([]);
        }
      } else {
        setRelated([]);
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Content could not be loaded");
    } finally {
      setLoading(false);
    }
  }, [contentId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const refresh = () => void load();
    window.addEventListener("fanbackstage:entitlements-changed", refresh);
    return () => window.removeEventListener("fanbackstage:entitlements-changed", refresh);
  }, [load]);

  const detailPath = useMemo(() => contentDetailPath(contentId), [contentId]);

  function requestPurchase() {
    if (!requireLogin({ nextPath: detailPath })) return;
    idempotencyKey.current ??= crypto.randomUUID();
    setPurchaseError("");
    setPurchaseStatus("");
    setPurchaseOpen(true);
  }

  async function purchase() {
    if (!content) return;
    setPurchasing(true);
    setPurchaseError("");
    try {
      idempotencyKey.current = purchaseAttemptKey(idempotencyKey.current);
      const started = await api<{ payment_attempt_id: string; status: string }>(`/purchases/content/${content.id}`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey.current },
      });
      if (purchasePaymentRequiresNewKey(started.status)) {
        idempotencyKey.current = null;
        setPurchaseError("The previous payment attempt failed. Confirm again to start a new, safely tracked attempt.");
        return;
      }
      if (process.env.NODE_ENV !== "production") {
        await api(`/payments/development/${started.payment_attempt_id}/complete`, { method: "POST" });
        await load();
        setPurchaseStatus("Purchase confirmed. Full access is now active.");
        window.dispatchEvent(new Event("fanbackstage:entitlements-changed"));
      } else {
        setPurchaseStatus("Payment started. Access will unlock after the configured provider confirms the charge.");
      }
      setPurchaseOpen(false);
      idempotencyKey.current = null;
    } catch (caught) {
      setPurchaseError(
        caught instanceof ApiError && caught.status === 409
          ? "This purchase changed while it was being confirmed. Refresh access and retry safely."
          : "Purchase could not be completed. No access change has been assumed.",
      );
    } finally {
      setPurchasing(false);
    }
  }

  if (loading) return <div className={styles.loading}><Skeleton lines={3} /><Skeleton /></div>;
  if (error || !content) return <EmptyState action={<Link className={styles.secondaryLink} href="/discover">Return to Discover</Link>} body={error || "This content is not available."} title="Content unavailable" />;

  const creatorHref = content.creator_username ? `/creator/${encodeURIComponent(content.creator_username)}` : "/creators";
  const price = content.price_amount_minor != null && content.price_currency
    ? formatMoney(content.price_amount_minor, content.price_currency)
    : null;

  return (
    <article className={styles.detailShell}>
      <header className={styles.detailHeader}>
        <Link className={styles.creatorLink} href={creatorHref}>
          <CreatorAvatar displayName={content.creator_display_name ?? "Creator"} size={46} username={content.creator_username ?? undefined} />
          <span><small>BY</small><strong>{content.creator_display_name ?? "FanBackstage creator"}</strong>{content.creator_username && <em>@{content.creator_username}</em>}</span>
        </Link>
        <div className={styles.headerBadges}>
          <AccessBadge locked={content.locked} policy={content.access_policy} />
          <span>{content.content_type === "gallery" ? `${content.media_count} photos` : durationLabel(content.duration_seconds) ?? "Video"}</span>
        </div>
      </header>

      {!content.compliance_allowed || (content.adult_access_required && !content.adult_access_granted) ? (
        <AdultAccessGate
          access={content}
          adultRestricted={Boolean(content.adult_access_required)}
          feature={content.adult_access_required ? "adult_media" : "platform_access"}
          onGranted={load}
          title={content.title}
        />
      ) : content.content_type === "gallery" ? (
        <GalleryViewer content={content} />
      ) : (
        <VideoViewer content={content} />
      )}

      <div className={styles.detailBody}>
        <div>
          <p className={styles.eyebrow}>{content.content_type === "gallery" ? "CREATOR GALLERY" : "CREATOR VIDEO"}</p>
          <h1>{content.title}</h1>
          {content.description && <p className={styles.description}>{content.description}</p>}
          {content.published_at && <p className={styles.published}>Published {new Date(content.published_at).toLocaleDateString()}</p>}
        </div>
        <aside className={styles.accessPanel}>
          {!content.compliance_allowed || (content.adult_access_required && !content.adult_access_granted) ? (
            <><strong>Verification required</strong><p>{content.compliance_reason ?? "Complete the access check above before entitlement or purchase options can be evaluated."}</p></>
          ) : content.has_access ? (
            <><strong>Access confirmed</strong><p>{content.access_policy === "free" ? "This creator made the complete release free to view." : "Your current entitlement unlocks the complete release."}</p></>
          ) : content.access_policy === "ppv" && price ? (
            <><strong>{price}</strong><p>One-time unlock for this complete {content.content_type}. Your purchase is recorded with an immutable receipt.</p><button className={styles.primaryButton} disabled={authLoading} onClick={requestPurchase} type="button">{authenticated ? `Unlock for ${price}` : `Log in to unlock · ${price}`}</button></>
          ) : content.access_policy === "subscription" && content.creator_id && content.creator_username ? (
            <SubscriptionOptions creatorId={content.creator_id} onActivated={() => void load()} username={content.creator_username} />
          ) : content.access_policy === "followers" ? (
            <><strong>Followers only</strong><p>Follow this creator from their profile to request access through the existing follow policy.</p><Link className={styles.primaryLink} href={creatorHref}>View creator profile</Link></>
          ) : (
            <><strong>Private release</strong><p>This item requires a direct entitlement and is not available for open purchase.</p></>
          )}
          {purchaseStatus && <p className={styles.success} role="status">{purchaseStatus}</p>}
        </aside>
      </div>

      <RelatedContent current={content} items={related} />

      <PurchaseDialog content={content} error={purchaseError} onClose={() => !purchasing && setPurchaseOpen(false)} onConfirm={() => void purchase()} open={purchaseOpen} working={purchasing} />
    </article>
  );
}
