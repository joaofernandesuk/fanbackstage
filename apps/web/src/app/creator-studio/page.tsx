"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { api, ApiError } from "../../lib/api";
import { CreatorEarnings } from "../../components/creator-earnings";
import { CreatorStudioProgress } from "../../components/creator-studio-progress";
import { MassMessageCampaign } from "../../components/mass-message-campaign";
import { SubscriptionSettings } from "../../components/subscription-settings";
import { SubscriptionPromotionSettings } from "../../components/subscription-promotion-settings";
import { MessagingSettings } from "../../components/messaging-settings";
import { LiveStudio } from "../../components/live-studio";
import { LiveVipStudio } from "../../components/live-vip-studio";
import { LivePaidRequestStudio } from "../../components/live-paid-request-studio";
import { PrivateSessionQueue } from "../../components/private-session-queue";
import { GroupMemberships } from "../../components/group-memberships";
import { MarketplaceFulfilment } from "../../components/marketplace-fulfilment";
import { ReferralDashboard } from "../../components/referral-dashboard";
import { MediaDropzone } from "../../components/media-dropzone";
import { CreatorProfileMediaEditor } from "../../components/creator-profile-media";
import { MarketplaceAuthoring } from "../../components/marketplace-authoring";
import { LiveCommerceSettings } from "../../components/live-commerce-settings";
import styles from "./creator-studio.module.css";

type Asset = { id: string; status: string; media_type?: string; display_path?: string };
type Upload = Asset & { upload_url?: string };
type Content = { id: string; title: string; content_type: string; status: string; access_policy: string; price_amount_minor?: number | null; price_currency?: string | null };
type FeedPost = { id: string; body: string | null; status: string; pinned_at: string | null; access_policy: string };
type CreatorAccess = {
  status: string;
  is_public: boolean;
  creator_compliance: { public_allowed: boolean; reason: string };
};
type StudioWorkspace = "overview" | "publish" | "library" | "live" | "audience" | "business";
type LiveTool = "vip" | "commerce" | "paid-requests" | "private-queue";

const policies = ["free", "followers", "subscription", "ppv", "private"];
const workspaceForHash: Record<string, StudioWorkspace> = {
  "#posts": "publish",
  "#media-content": "library",
  "#live": "live",
  "#subscriptions": "audience",
  "#marketplace-fulfilment": "business",
};

export default function CreatorStudioPage() {
  return <CreatorStudio />;
}

function CreatorStudio() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [content, setContent] = useState<Content[]>([]);
  const [posts, setPosts] = useState<FeedPost[]>([]);
  const [creatorAccess, setCreatorAccess] = useState<CreatorAccess | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadResetToken, setUploadResetToken] = useState(0);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [workspace, setWorkspace] = useState<StudioWorkspace>("overview");
  const [liveTool, setLiveTool] = useState<LiveTool | null>(null);
  const readyImages = assets.filter((asset) => asset.status === "ready" && asset.media_type === "image");
  const readyVideos = assets.filter((asset) => asset.status === "ready" && asset.media_type === "video");

  async function refresh() {
    const [media, managedContent, managedPosts, creator] = await Promise.all([
      api<Asset[]>("/media/mine"),
      api<Content[]>("/content/mine"),
      api<{ items: FeedPost[] }>("/feed/mine"),
      api<CreatorAccess>("/creators/me"),
    ]);
    setAssets(media);
    setContent(managedContent);
    setPosts(managedPosts.items);
    setCreatorAccess(creator);
  }

  useEffect(() => { refresh().catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Unable to load creator media")); }, []);

  useEffect(() => {
    const syncWorkspaceFromHash = () => {
      setWorkspace(workspaceForHash[window.location.hash] ?? "overview");
    };
    syncWorkspaceFromHash();
    window.addEventListener("hashchange", syncWorkspaceFromHash);
    return () => window.removeEventListener("hashchange", syncWorkspaceFromHash);
  }, []);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const file = uploadFile;
    if (!file) { setError("Drop a media file or choose one from your device."); return; }
    try {
      const created = await api<Upload>("/media/uploads", { method: "POST", body: JSON.stringify({ filename: file.name, mime_type: file.type }) });
      if (!created.upload_url) throw new Error("The upload authorization is missing");
      const uploadResponse = await fetch(created.upload_url, { method: "PUT", headers: { "Content-Type": file.type }, body: file });
      if (!uploadResponse.ok) throw new Error(`Storage upload failed (${uploadResponse.status})`);
      await api<Upload>(`/media/${created.id}/finalize`, { method: "POST" });
      setMessage("Upload queued for processing.");
      setUploadFile(null);
      setUploadResetToken((current) => current + 1);
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Upload failed"); }
  }

  async function createGallery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const selected = form.getAll("image").map(String);
    if (!selected.length) { setError("Choose at least one ready image."); return; }
    try {
      const policy = String(form.get("gallery-policy"));
      const price = String(form.get("gallery-price-minor") || "");
      const price_amount_minor = price ? Number(price) : undefined;
      if (policy === "ppv" && price_amount_minor === undefined) throw new Error("PPV content requires a price.");
      if (price && (price_amount_minor === undefined || !Number.isSafeInteger(price_amount_minor) || price_amount_minor <= 0)) throw new Error("Price must be a positive whole number of minor units.");
      const gallery = await api<Content>("/content/galleries", { method: "POST", body: JSON.stringify({ title: form.get("gallery-title"), description: String(form.get("gallery-description") || "") || undefined, access_policy: policy, ...(policy === "ppv" ? { price_amount_minor, price_currency: String(form.get("gallery-currency") || "EUR").toUpperCase() } : {}) }) });
      for (const media_asset_id of selected) await api(`/content/galleries/${gallery.id}/items`, { method: "POST", body: JSON.stringify({ media_asset_id }) });
      const cover = String(form.get("gallery-cover") || selected[0]);
      const previewCount = Number(form.get("gallery-preview-count") || 0);
      if (!selected.includes(cover)) { setError("The cover image must be one of the selected gallery images."); return; }
      await api(`/content/galleries/${gallery.id}/cover`, { method: "PATCH", body: JSON.stringify({ media_asset_id: cover }) });
      await api(`/content/galleries/${gallery.id}/preview`, { method: "PATCH", body: JSON.stringify({ preview_count: previewCount, preview_asset_ids: [] }) });
      await api(`/content/${gallery.id}/submit`, { method: "POST" });
      setMessage("Gallery submitted for review with its secure preview configuration.");
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Gallery could not be published"); }
  }

  async function createVideo(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const mediaAssetId = String(form.get("video") || "");
      if (!mediaAssetId) throw new Error("Choose a ready video.");
      const policy = String(form.get("video-policy"));
      const price = String(form.get("video-price-minor") || "");
      const price_amount_minor = price ? Number(price) : undefined;
      if (policy === "ppv" && price_amount_minor === undefined) throw new Error("PPV content requires a price.");
      if (price && (price_amount_minor === undefined || !Number.isSafeInteger(price_amount_minor) || price_amount_minor <= 0)) throw new Error("Price must be a positive whole number of minor units.");
      await api<Content>("/content/videos", { method: "POST", body: JSON.stringify({ title: form.get("video-title"), description: String(form.get("video-description") || "") || undefined, access_policy: policy, media_asset_id: mediaAssetId, preview_start_seconds: Number(form.get("video-preview-start") || 0), preview_duration_seconds: Number(form.get("video-preview-duration") || 2), ...(policy === "ppv" ? { price_amount_minor, price_currency: String(form.get("video-currency") || "EUR").toUpperCase() } : {}) }) });
      setMessage("Video preview rendering is queued. Submit it for review once processing is complete.");
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Video could not be published"); }
  }

  async function submitForReview(contentId: string) {
    try {
      await api(`/content/${contentId}/submit`, { method: "POST" });
      setMessage("Content submitted for review.");
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Content is not ready for review"); }
  }

  async function createPost(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const media_asset_ids = form.getAll("post-media").map(String);
      const content_id = String(form.get("post-content") || "") || undefined;
      const scheduled = String(form.get("post-scheduled-at") || "");
      const post = await api<{ id: string }>("/feed/posts", { method: "POST", body: JSON.stringify({ post_type: content_id ? "gallery_reference" : media_asset_ids.length ? "mixed_media" : "text", body: form.get("post-body"), media_asset_ids, content_id, access_policy: form.get("post-policy"), comments_enabled: form.get("post-comments") === "on", scheduled_at: scheduled ? new Date(scheduled).toISOString() : undefined }) });
      if (form.get("publish-now") === "on" && !scheduled) await api(`/feed/posts/${post.id}/publish`, { method: "POST" });
      setMessage("Post saved."); event.currentTarget.reset();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Post could not be saved"); }
  }

  async function postAction(postId: string, action: "pin" | "unpin" | "archive") {
    try { await api(`/feed/posts/${postId}/${action}`, { method: action === "unpin" ? "DELETE" : "POST" }); await refresh(); } catch (e) { setError(e instanceof ApiError ? e.message : "Post could not be updated"); }
  }

  const selectWorkspace = (next: StudioWorkspace) => {
    setWorkspace(next);
    window.history.replaceState(null, "", next === "overview" ? "/creator-studio" : `/creator-studio#${
      next === "publish" ? "posts" : next === "library" ? "media-content" : next === "audience" ? "subscriptions" : next === "business" ? "marketplace-fulfilment" : "live"
    }`);
  };
  const nav: Array<[StudioWorkspace, string, string]> = [
    ["overview", "Overview", "Your next actions and earnings"],
    ["publish", "Publish", "Posts and Stories"],
    ["library", "Media library", "Uploads, galleries, and videos"],
    ["live", "Live", "Public rooms and private sessions"],
    ["audience", "Audience", "Subscribers and messages"],
    ["business", "Business", "Orders, groups, and referrals"],
  ];
  const liveReady = Boolean(
    creatorAccess?.status === "approved" &&
    creatorAccess.is_public &&
    creatorAccess.creator_compliance.public_allowed,
  );
  const publicationIsOnlyRemainingLiveStep = Boolean(
    creatorAccess?.status === "approved" &&
    !creatorAccess.is_public &&
    creatorAccess.creator_compliance.public_allowed,
  );

  return (
    <section className={`card ${styles.studio}`}>
      <header className={styles.header}>
        <div>
          <p className="eyebrow">CREATOR STUDIO</p>
          <h1>Your studio</h1>
          <p>Choose one workspace at a time. Your publishing tools, audience, and business controls stay organised here.</p>
        </div>
        <div className={styles.quickActions}>
          <Link className={styles.primaryAction} href="/creator-studio/stories">Create a Story</Link>
          <button onClick={() => selectWorkspace("publish")} type="button">Create a post</button>
          <button onClick={() => selectWorkspace("live")} type="button">Go live</button>
        </div>
      </header>

      <nav aria-label="Creator Studio workspaces" className={styles.workspaceNav}>
        {nav.map(([key, label, description]) => (
          <button aria-current={workspace === key ? "page" : undefined} className={workspace === key ? styles.activeWorkspace : undefined} key={key} onClick={() => selectWorkspace(key)} type="button">
            <strong>{label}</strong><span>{description}</span>
          </button>
        ))}
      </nav>

      {message && <p className={styles.success} role="status">{message}</p>}
      {error && <p className={styles.error} role="alert">{error}</p>}

      {workspace === "overview" && <div className={styles.workspace}>
        <CreatorStudioProgress />
        <CreatorEarnings />
        <section className={styles.emptyAction}><h2>Ready to share?</h2><p>Create a Story for a quick 24-hour update, publish a post, or add a gallery or video from your media library.</p><div><Link className={styles.primaryAction} href="/creator-studio/stories">Create a Story</Link><button onClick={() => selectWorkspace("library")} type="button">Open media library</button></div></section>
      </div>}

      {workspace === "publish" && <div className={styles.workspace}>
        <section className={styles.panel}><div className={styles.panelHeading}><div><p className="eyebrow">POSTS</p><h2>Create a post</h2><p>Share an update, attach ready media, or point fans to a published gallery or video.</p></div><Link className={styles.primaryAction} href="/creator-studio/stories">Story composer</Link></div>
          <form className={styles.form} onSubmit={createPost}><label>Post text<textarea name="post-body" required maxLength={5000} /></label><div className={styles.twoColumns}><label>Access policy<select name="post-policy">{policies.slice(0, 4).map((policy) => <option key={policy}>{policy}</option>)}</select></label><label>Schedule (UTC)<input name="post-scheduled-at" type="datetime-local" /></label></div><label>Reference a published gallery or video<select name="post-content"><option value="">No reference</option>{content.filter(item => item.status === "published").map(item => <option key={item.id} value={item.id}>{item.content_type}: {item.title}</option>)}</select></label><fieldset><legend>Attach ready media</legend>{assets.filter(asset => asset.status === "ready").map(asset => <label key={asset.id}><input name="post-media" type="checkbox" value={asset.id} />{asset.media_type}: {asset.id}</label>)}{!assets.some(asset => asset.status === "ready") && <p>No ready media yet. Upload an image or video first.</p>}</fieldset><div className={styles.inlineChecks}><label><input name="post-comments" type="checkbox" defaultChecked /> Allow comments</label><label><input name="publish-now" type="checkbox" defaultChecked /> Publish now</label></div><button className={styles.primaryAction}>Save post</button></form>
        </section>
        <section className={styles.panel}><h2>Recent posts</h2>{posts.length ? <ul className={styles.recordList}>{posts.map(post => <li key={post.id}><div><strong>{post.status}</strong><span>{post.body ?? "Media post"}</span></div><div><button onClick={() => postAction(post.id, post.pinned_at ? "unpin" : "pin")} type="button">{post.pinned_at ? "Unpin" : "Pin"}</button><button onClick={() => postAction(post.id, "archive")} type="button">Archive</button></div></li>)}</ul> : <p>No posts yet.</p>}</section>
      </div>}

      {workspace === "library" && <div className={styles.workspace}>
        <section className={styles.panel}><div className={styles.panelHeading}><div><p className="eyebrow">MEDIA LIBRARY</p><h2>Upload media</h2><p>Drop an image or video here, or browse your device. Images can be cropped for the card or surface you plan to use; video stays untouched.</p></div></div><form className={styles.form} onSubmit={upload}><MediaDropzone accept="image/jpeg,image/png,image/webp,video/mp4,video/webm" onChange={setUploadFile} resetToken={uploadResetToken} /><button className={styles.primaryAction} disabled={!uploadFile}>Upload media</button></form></section>
        <div className={styles.twoPanels}><section className={styles.panel}><h2>Create a gallery</h2><form className={styles.form} onSubmit={createGallery}><label>Gallery title<input name="gallery-title" required /></label><label>Description<textarea name="gallery-description" maxLength={5000} /></label><label>Access policy<select name="gallery-policy">{policies.map((policy) => <option key={policy}>{policy}</option>)}</select></label><div className={styles.twoColumns}><label>PPV price (minor units)<input name="gallery-price-minor" type="number" min="1" step="1" /></label><label>Currency<input name="gallery-currency" defaultValue="EUR" maxLength={3} /></label></div><label>Public preview images<input name="gallery-preview-count" type="number" min="0" max="100" defaultValue="1" /></label><label>Cover image<select name="gallery-cover"><option value="">First selected image</option>{readyImages.map((asset) => <option key={asset.id} value={asset.id}>{asset.id}</option>)}</select></label><fieldset><legend>Ready images</legend>{readyImages.map((asset) => <label key={asset.id}><input name="image" type="checkbox" value={asset.id} />{asset.id}</label>)}</fieldset><button className={styles.primaryAction}>Create and submit gallery</button></form></section>
        <section className={styles.panel}><h2>Create a video</h2><form className={styles.form} onSubmit={createVideo}><label>Video title<input name="video-title" required /></label><label>Description<textarea name="video-description" maxLength={5000} /></label><label>Ready video<select name="video" required defaultValue=""><option value="" disabled>{readyVideos.length ? "Choose a processed video" : "Upload and process a video first"}</option>{readyVideos.map((asset) => <option key={asset.id} value={asset.id}>{asset.id}</option>)}</select></label><label>Access policy<select name="video-policy">{policies.map((policy) => <option key={policy}>{policy}</option>)}</select></label><div className={styles.twoColumns}><label>PPV price (minor units)<input name="video-price-minor" type="number" min="1" step="1" /></label><label>Currency<input name="video-currency" defaultValue="EUR" maxLength={3} /></label></div><div className={styles.twoColumns}><label>Preview starts at (seconds)<input name="video-preview-start" type="number" min="0" defaultValue="0" /></label><label>Preview duration (seconds)<input name="video-preview-duration" type="number" min="1" max="120" defaultValue="2" /></label></div><button className={styles.primaryAction}>Create video</button></form></section></div>
        <section className={styles.panel}><CreatorProfileMediaEditor assets={assets} onSaved={refresh} /></section>
        <section className={styles.panel}><h2>Library status</h2>{assets.length ? <ul className={styles.recordList}>{assets.map(asset => <li key={asset.id}><strong>{asset.media_type ?? "media"}</strong><span>{asset.id}</span><em>{asset.status}</em></li>)}</ul> : <p>Your uploads will appear here.</p>}<h2>Content</h2>{content.length ? <ul className={styles.recordList}>{content.map(item => <li key={item.id}><strong>{item.content_type}</strong><span>{item.title}</span><em>{item.status}</em>{item.status !== "published" && <button onClick={() => submitForReview(item.id)} type="button">Submit for review</button>}</li>)}</ul> : <p>Your galleries and videos will appear here.</p>}</section>
      </div>}

      {workspace === "live" && <div className={styles.workspace}>
        {!liveReady ? (
          <section className={styles.emptyAction}>
            <p className="eyebrow">LIVE SETUP</p>
            <h2>{publicationIsOnlyRemainingLiveStep ? "Publish your creator profile to go live" : "Finish your public creator profile before going live"}</h2>
            {publicationIsOnlyRemainingLiveStep ? (
              <>
                <p>Your creator identity and age checks are complete. Your profile is currently private, so Live is intentionally unavailable to fans.</p>
                <ol className={styles.readinessSteps}>
                  <li>Open <strong>Profile publishing</strong>.</li>
                  <li>Turn on <strong>Make my approved creator profile public</strong>.</li>
                  <li>Select <strong>Save profile</strong>, then return here to start a public or private session.</li>
                </ol>
              </>
            ) : (
              <p>{creatorAccess?.creator_compliance.reason ?? "Your creator eligibility is still loading."} Complete the remaining profile requirements, then return here to start public or private sessions.</p>
            )}
            <Link className={styles.primaryAction} href="/creator-onboarding#publication">Open profile publishing</Link>
          </section>
        ) : <>
          <LiveStudio />
          <section aria-label="Live setup tools" className={styles.liveControlCenter}>
            <div className={styles.panelHeading}>
              <div><p className="eyebrow">LIVE CONTROL CENTER</p><h2>Prepare the show</h2><p>Open one tool when you need it. Your camera and chat stay at the top.</p></div>
            </div>
            <div className={styles.liveControlGrid}>
              <button onClick={() => setLiveTool("vip")} type="button"><span aria-hidden="true">★</span><strong>VIP mode</strong><small>Plan or control a paid group show</small></button>
              <button onClick={() => setLiveTool("commerce")} type="button"><span aria-hidden="true">◎</span><strong>Tips, snapshots & goals</strong><small>Preview catalogues and set creator options</small></button>
              <button onClick={() => setLiveTool("paid-requests")} type="button"><span aria-hidden="true">☷</span><strong>Paid requests</strong><small>Set the menu and review requests</small></button>
              <button onClick={() => setLiveTool("private-queue")} type="button"><span aria-hidden="true">◉</span><strong>Private queue</strong><small>Review queued 1:1 and 2-to-1 sessions</small></button>
            </div>
          </section>
          {liveTool && (
            <div className={styles.liveToolModal} onMouseDown={() => setLiveTool(null)}>
              <section aria-label="Live setup panel" aria-modal="true" className={styles.liveToolPanel} onMouseDown={(event) => event.stopPropagation()} role="dialog">
                <header><strong>Live setup</strong><button aria-label="Close live setup" onClick={() => setLiveTool(null)} type="button">×</button></header>
                {liveTool === "vip" && <LiveVipStudio />}
                {liveTool === "commerce" && <LiveCommerceSettings />}
                {liveTool === "paid-requests" && <LivePaidRequestStudio />}
                {liveTool === "private-queue" && <PrivateSessionQueue />}
              </section>
            </div>
          )}
        </>}
      </div>}
      {workspace === "audience" && <div className={styles.workspace}><SubscriptionSettings /><SubscriptionPromotionSettings /><MessagingSettings /><MassMessageCampaign /></div>}
      {workspace === "business" && <div className={styles.workspace}><MarketplaceAuthoring assets={assets} /><div id="marketplace-fulfilment"><MarketplaceFulfilment /></div><GroupMemberships /><ReferralDashboard heading="Creator referral earnings" /></div>}
    </section>
  );
}
