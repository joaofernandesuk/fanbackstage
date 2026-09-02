"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";

import { ApiError, api } from "../lib/api";
import { completePaymentCheckout } from "../lib/payments";
import { marketplaceListingMediaUrl } from "../lib/content-api";
import {
  formatMoney,
  type MarketplaceListing,
} from "../lib/public-api";
import { CreatorAvatar, EmptyState, LoginGate, Skeleton, VerifiedBadge } from "./consumer-ui";
import styles from "./marketplace-detail.module.css";

export type MarketplaceCheckoutDraft = {
  quantity: number;
  recipientName: string;
  line1: string;
  line2: string;
  city: string;
  regionCode: string;
  postalCode: string;
  countryCode: string;
};

type MarketplaceOrder = {
  id: string;
  public_id: string;
  status: string;
  quantity: number;
  currency: string;
  item_subtotal_minor: number;
  shipping_charged_minor: number;
  total_paid_minor: number;
  payment_attempt_id: string;
};

type CheckoutStep = "idle" | "address" | "review" | "success";

export function marketplaceCheckoutPayload(draft: MarketplaceCheckoutDraft) {
  const countryCode = draft.countryCode.trim().toUpperCase();
  const regionCode = draft.regionCode.trim().toUpperCase();
  const line2 = draft.line2.trim();
  return {
    quantity: draft.quantity,
    destination_country_code: countryCode,
    ...(regionCode ? { destination_region_code: regionCode } : {}),
    shipping_address: {
      recipient_name: draft.recipientName.trim(),
      line1: draft.line1.trim(),
      ...(line2 ? { line2 } : {}),
      city: draft.city.trim(),
      ...(regionCode ? { region_code: regionCode } : {}),
      postal_code: draft.postalCode.trim(),
      country_code: countryCode,
    },
  };
}

export function marketplaceCheckoutError(caught: unknown) {
  if (caught instanceof ApiError) {
    if (caught.code === "marketplace_payment_terminal") return "Payment was not completed. Retry to create a new, stock-checked order.";
    if (caught.status === 400) return "The delivery details or quantity could not be accepted. Review them and retry safely.";
    if (caught.status === 403) return "Checkout is not available for this account or creator relationship.";
    if (caught.status === 409) return "Listing availability changed during checkout. Refresh the listing before retrying.";
    if (caught.status === 429) return "Too many checkout attempts. Wait a moment before retrying.";
  }
  return "Checkout could not be completed. Your order status has not been hidden; retry safely or review your orders.";
}

export function marketplaceCheckoutRequiresNewKey(caught: unknown) {
  return caught instanceof ApiError && caught.code === "marketplace_payment_terminal";
}

export function MarketplaceDetail({ publicId }: { publicId: string }) {
  const [listing, setListing] = useState<MarketplaceListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [step, setStep] = useState<CheckoutStep>("idle");
  const [draft, setDraft] = useState<MarketplaceCheckoutDraft | null>(null);
  const [order, setOrder] = useState<MarketplaceOrder | null>(null);
  const [checkoutErrorMessage, setCheckoutErrorMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const idempotencyKey = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    api<MarketplaceListing>(`/marketplace/listings/${encodeURIComponent(publicId)}`).then((result) => {
      if (!active) return;
      setListing(result);
      setLoading(false);
    }).catch((caught) => {
      if (!active) return;
      setError(
        caught instanceof ApiError && caught.status === 404
          ? "Marketplace listing not found"
          : "Marketplace listing could not be loaded. Try again shortly.",
      );
      setLoading(false);
    });
    return () => { active = false; };
  }, [publicId]);

  function reviewAddress(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setCheckoutErrorMessage("");
    setDraft({
      quantity: Number(form.get("quantity")),
      recipientName: String(form.get("recipient-name") || ""),
      line1: String(form.get("address-line-1") || ""),
      line2: String(form.get("address-line-2") || ""),
      city: String(form.get("city") || ""),
      regionCode: String(form.get("region-code") || ""),
      postalCode: String(form.get("postal-code") || ""),
      countryCode: String(form.get("country-code") || ""),
    });
    setStep("review");
  }

  async function confirmOrder() {
    if (!draft) return;
    setSubmitting(true);
    setCheckoutErrorMessage("");
    try {
      idempotencyKey.current ??= window.crypto.randomUUID();
      const reserved = await api<MarketplaceOrder>(
        `/marketplace/listings/${encodeURIComponent(publicId)}/checkout`,
        {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey.current },
          body: JSON.stringify(marketplaceCheckoutPayload(draft)),
        },
      );
      await completePaymentCheckout(reserved.payment_attempt_id);
      const refreshed = await api<MarketplaceOrder>(`/marketplace/orders/${reserved.id}`)
        .catch(() => reserved);
      setOrder(refreshed);
      setStep("success");
    } catch (caught) {
      if (marketplaceCheckoutRequiresNewKey(caught)) idempotencyKey.current = null;
      setCheckoutErrorMessage(marketplaceCheckoutError(caught));
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <div className={styles.loading}><Skeleton lines={4} /><Skeleton lines={5} /></div>;
  if (error || !listing) return <EmptyState action={<Link className={styles.backButton} href="/marketplace">Back to marketplace</Link>} body={error || "This listing is not publicly available."} title="Listing unavailable" />;

  const seller = listing.seller ?? null;
  const username = seller?.username;
  const mediaUrl = marketplaceListingMediaUrl(listing.media);
  const soldOut = listing.quantity_available === 0 || listing.status === "sold_out";
  const shipping = listing.shipping_charged_minor === 0
    ? "Shipping included"
    : `${formatMoney(listing.shipping_charged_minor, listing.currency)} shipping`;
  const detailPath = `/marketplace/${encodeURIComponent(publicId)}`;

  return (
    <div className={styles.detail}>
      <nav aria-label="Breadcrumb" className={styles.breadcrumb}><Link href="/marketplace">Marketplace</Link><span aria-hidden="true">/</span><span>{listing.title}</span></nav>
      <div className={styles.layout}>
        <section aria-label={`${listing.title} image`} className={styles.gallery}>
          {mediaUrl ? <img alt={`${listing.title} from ${seller?.display_name ?? "a FanBackstage creator"}`} src={mediaUrl} /> : <span className={styles.fallback} />}
          <span className={styles.category}>{listing.category.replaceAll("_", " ")}</span>
        </section>
        <section className={styles.summary}>
          <p className={styles.kicker}>Creator marketplace</p>
          <h1>{listing.title}</h1>
          <p className={styles.price}>{formatMoney(listing.price_amount_minor, listing.currency)}</p>
          <p className={styles.description}>{listing.description || "A creator-owned FanBackstage marketplace item."}</p>
          <dl className={styles.facts}>
            <div><dt>Condition</dt><dd>{listing.condition.replaceAll("_", " ")}</dd></div>
            <div><dt>Availability</dt><dd>{soldOut ? "Sold out" : `${listing.quantity_available} available`}</dd></div>
            <div><dt>Ships from</dt><dd>{listing.origin_country_code}</dd></div>
            <div><dt>Delivery</dt><dd>{shipping}</dd></div>
          </dl>

          {step === "idle" && (soldOut
            ? <button className={styles.disabled} disabled type="button">Sold out</button>
            : (
              <LoginGate className={styles.buyButton} label="Log in to buy this item" nextPath={detailPath}>
                <button className={styles.buyButton} onClick={() => setStep("address")} type="button">
                  Buy this item
                </button>
              </LoginGate>
            ))}

          {step === "address" && (
            <section aria-labelledby="delivery-heading" className={styles.checkoutPanel}>
              <div className={styles.checkoutHeading}>
                <span>Step 1 of 2</span>
                <h2 id="delivery-heading">Delivery details</h2>
                <p>The address is restricted to authorised order fulfilment roles.</p>
              </div>
              <form className={styles.checkoutForm} onSubmit={reviewAddress}>
                <label>Quantity<input defaultValue={draft?.quantity ?? 1} max={listing.quantity_available} min="1" name="quantity" required type="number" /></label>
                <label>Recipient name<input autoComplete="name" defaultValue={draft?.recipientName} maxLength={160} name="recipient-name" required /></label>
                <label className={styles.fullWidth}>Address line 1<input autoComplete="address-line1" defaultValue={draft?.line1} maxLength={160} name="address-line-1" required /></label>
                <label className={styles.fullWidth}>Address line 2 <small>Optional</small><input autoComplete="address-line2" defaultValue={draft?.line2} maxLength={160} name="address-line-2" /></label>
                <label>City<input autoComplete="address-level2" defaultValue={draft?.city} maxLength={120} name="city" required /></label>
                <label>Region code <small>Optional</small><input autoComplete="address-level1" defaultValue={draft?.regionCode} maxLength={16} name="region-code" /></label>
                <label>Postal code<input autoComplete="postal-code" defaultValue={draft?.postalCode} maxLength={32} name="postal-code" required /></label>
                <label>Country code<input autoComplete="country" defaultValue={draft?.countryCode} maxLength={2} minLength={2} name="country-code" placeholder="PT" required /></label>
                <div className={styles.checkoutActions}>
                  <button className={styles.secondaryButton} onClick={() => setStep("idle")} type="button">Cancel</button>
                  <button className={styles.buyButton} type="submit">Review order</button>
                </div>
              </form>
            </section>
          )}

          {step === "review" && draft && (
            <section aria-labelledby="review-heading" className={styles.checkoutPanel}>
              <div className={styles.checkoutHeading}>
                <span>Step 2 of 2</span>
                <h2 id="review-heading">Review order</h2>
                <p>Nothing is submitted until you confirm below.</p>
              </div>
              <dl className={styles.reviewList}>
                <div><dt>Item</dt><dd>{listing.title} × {draft.quantity}</dd></div>
                <div><dt>Listed item amount</dt><dd>{formatMoney(listing.price_amount_minor * draft.quantity, listing.currency)}</dd></div>
                <div><dt>Listed shipping</dt><dd>{formatMoney(listing.shipping_charged_minor, listing.currency)}</dd></div>
                <div><dt>Deliver to</dt><dd>{draft.recipientName}<br />{draft.line1}{draft.line2 ? <><br />{draft.line2}</> : null}<br />{draft.postalCode} {draft.city}, {draft.countryCode.toUpperCase()}</dd></div>
              </dl>
              <p className={styles.providerNote}>The server validates delivery, reserves stock, and returns the authoritative total. The configured payment provider must confirm payment; FanBackstage does not use wallet or credits for this checkout.</p>
              {checkoutErrorMessage && <p className={styles.checkoutError} role="alert">{checkoutErrorMessage}</p>}
              <div className={styles.checkoutActions}>
                <button className={styles.secondaryButton} disabled={submitting} onClick={() => setStep("address")} type="button">Edit address</button>
                <button className={styles.buyButton} disabled={submitting} onClick={() => void confirmOrder()} type="button">
                  {submitting ? "Confirming…" : process.env.NODE_ENV === "production" ? "Place order" : "Place order and confirm payment"}
                </button>
              </div>
            </section>
          )}

          {step === "success" && order && (
            <section aria-labelledby="success-heading" className={styles.checkoutPanel}>
              <div className={styles.successIcon} aria-hidden="true">✓</div>
              <div className={styles.checkoutHeading}>
                <span>ORDER {order.public_id}</span>
                <h2 id="success-heading">{order.status === "paid" ? "Order confirmed" : "Order placed"}</h2>
                <p>{order.status === "paid" ? "Payment is confirmed and the creator can begin fulfilment." : "Payment is awaiting confirmation from the configured provider."}</p>
              </div>
              <dl className={styles.serverTotals}>
                <div><dt>Items</dt><dd>{formatMoney(order.item_subtotal_minor, order.currency)}</dd></div>
                <div><dt>Shipping</dt><dd>{formatMoney(order.shipping_charged_minor, order.currency)}</dd></div>
                <div><dt>Exact total</dt><dd>{formatMoney(order.total_paid_minor, order.currency)}</dd></div>
              </dl>
              <Link className={styles.buyButton} href="/marketplace/orders">View order status</Link>
            </section>
          )}

          {step === "idle" && <p className={styles.purchaseNote}>Delivery eligibility and exact totals are resolved by the marketplace checkout service.</p>}
          {seller && username && (
            <Link className={styles.seller} href={`/creator/${encodeURIComponent(username)}`}>
              <CreatorAvatar displayName={seller.display_name} size={48} />
              <span><small>Sold by</small><strong>{seller.display_name} {seller.verified && <VerifiedBadge />}</strong><em>@{username}</em></span>
              <b aria-hidden="true">→</b>
            </Link>
          )}
        </section>
      </div>
    </div>
  );
}
