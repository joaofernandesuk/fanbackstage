"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";

import { api, ApiError } from "../lib/api";
import styles from "./creator-onboarding.module.css";

type TaxonomyItem = { id: string; code: string; label: string };
type SocialLink = { label: string; url: string };

export type CreatorOnboardingProfile = {
  username: string | null;
  display_name: string | null;
  bio: string | null;
  country_code: string | null;
  region: string | null;
  city: string | null;
  show_location: boolean;
  timezone: string | null;
  status: string;
  is_public: boolean;
  verification_status: string;
  adult_verified: boolean;
  rejection_reason: string | null;
  languages: TaxonomyItem[];
  categories: TaxonomyItem[];
  social_links: SocialLink[];
  available_languages: TaxonomyItem[];
  available_categories: TaxonomyItem[];
  development_verification_available: boolean;
};

type PendingAction = "save" | "submit" | "verify" | null;

export function canRunDevelopmentVerification(profile: CreatorOnboardingProfile): boolean {
  return profile.status === "pending_verification"
    && profile.development_verification_available;
}

export function creatorHasCurrentVerification(profile: CreatorOnboardingProfile): boolean {
  return profile.verification_status === "verified" && profile.adult_verified;
}

export function creatorProfilePayload(form: FormData, canPublish: boolean): Record<string, unknown> {
  const labels = form.getAll("social_label").map((value) => String(value).trim());
  const urls = form.getAll("social_url").map((value) => String(value).trim());
  if (labels.length !== urls.length || labels.length > 12) {
    throw new Error("Social links could not be read safely.");
  }
  const socialLinks = labels.flatMap((label, index) => {
    const url = urls[index];
    if (!label && !url) return [];
    if (!label || !url) throw new Error("Every social link needs both a label and a URL.");
    return [{ label, url }];
  });
  if (new Set(socialLinks.map((link) => link.url)).size !== socialLinks.length) {
    throw new Error("Use each social link URL only once.");
  }

  const countryCode = String(form.get("country_code") ?? "").trim().toUpperCase();
  const optionalText = (name: string) => String(form.get(name) ?? "").trim() || null;
  return {
    username: String(form.get("username") ?? "").trim(),
    display_name: String(form.get("display_name") ?? "").trim(),
    bio: optionalText("bio"),
    country_code: countryCode || null,
    region: optionalText("region"),
    city: optionalText("city"),
    show_location: form.get("show_location") === "on",
    timezone: optionalText("timezone"),
    category_slugs: form.getAll("category_slugs").map(String),
    language_codes: form.getAll("language_codes").map(String),
    social_links: socialLinks,
    ...(canPublish ? { is_public: form.get("is_public") === "on" } : {}),
  };
}

function statusCopy(profile: CreatorOnboardingProfile): { heading: string; body: string } {
  switch (profile.status) {
    case "draft":
      return {
        heading: "Complete your application draft",
        body: "Save the profile fans will recognise, then submit it for identity review.",
      };
    case "pending_verification":
      return canRunDevelopmentVerification(profile)
        ? {
          heading: "Identity verification is next",
          body: "This local test environment exposes its development-only verification action.",
        }
        : {
          heading: "Identity verification is pending",
          body: "Development verification is disabled here. A configured provider must continue this application; there is no browser shortcut.",
        };
    case "pending_review":
      return {
        heading: "Your application is in review",
        body: "A moderator must approve it. This page checks for the decision automatically while it remains open.",
      };
    case "approved":
      if (!creatorHasCurrentVerification(profile)) {
        return {
          heading: "Creator verification needs attention",
          body: "Your profile remains unavailable until a current verified adult KYC outcome is recorded. Saved profile details and your publication preference remain intact.",
        };
      }
      return profile.is_public
        ? {
          heading: "Your creator profile is live",
          body: "Your approved profile is public. You can make it private again at any time.",
        }
        : {
          heading: "Approved — choose when to go public",
          body: "Approval does not publish your profile automatically. Review it, enable the public profile switch, and save.",
        };
    case "rejected":
      return {
        heading: "Your application needs changes",
        body: `${profile.rejection_reason ? `Review outcome: ${profile.rejection_reason} ` : ""}Saving profile changes does not restart review; no creator-owned reapply action is available yet. Follow the moderation notice or contact support.`,
      };
    case "suspended":
      return {
        heading: "Your creator profile is suspended",
        body: "The public profile is unavailable. Follow the moderation notice or support process before trying to publish again.",
      };
    default:
      return {
        heading: "Your creator profile is unavailable",
        body: "This application cannot be published in its current state. Contact support for the recorded next step.",
      };
  }
}

const SAFE_PROFILE_ERRORS = new Set([
  "Every social link needs both a label and a URL.",
  "Social links could not be read safely.",
  "Use each social link URL only once.",
  "Username is unavailable or invalid",
  "Category selections include unavailable values",
  "Category selections cannot contain blank values",
  "Category selections cannot contain duplicates",
  "Category selections cannot contain more than 12 values",
  "Category selections include an invalid value",
  "Language selections include unavailable values",
  "Language selections cannot contain blank values",
  "Language selections cannot contain duplicates",
  "Language selections cannot contain more than 12 values",
  "Language selections include an invalid value",
  "Every social link requires a label and URL",
  "Social link label or URL is too long",
  "Social link labels cannot contain control characters",
  "Social link URLs cannot be duplicated",
  "Social links cannot contain more than 12 values",
  "Social links require a valid HTTP or HTTPS URL",
  "Only approved creators can make a profile public",
]);

export function creatorOnboardingError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (SAFE_PROFILE_ERRORS.has(error.message)) return error.message;
    if (error.status === 401) return "Your session expired. Log in again to continue.";
    if (error.status === 403) return "This account cannot perform that creator action.";
    if (error.status === 404) return "This creator application or action is unavailable.";
    if (error.status === 409) return "That profile change conflicts with saved account state.";
    if (error.status === 422) return "Check the highlighted profile fields and try again.";
    if (error.status === 429) return "Too many attempts. Wait a moment and try again.";
    return fallback;
  }
  if (error instanceof Error && SAFE_PROFILE_ERRORS.has(error.message)) return error.message;
  return fallback;
}

export function CreatorOnboarding() {
  const formRef = useRef<HTMLFormElement>(null);
  const actionLock = useRef(false);
  const [profile, setProfile] = useState<CreatorOnboardingProfile | null>(null);
  const [socialLinks, setSocialLinks] = useState<SocialLink[]>([{ label: "", url: "" }]);
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function acceptProfile(next: CreatorOnboardingProfile, resetLinks = false) {
    setProfile(next);
    if (resetLinks) {
      setSocialLinks(next.social_links.length ? next.social_links : [{ label: "", url: "" }]);
    }
  }

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        let next: CreatorOnboardingProfile;
        try {
          next = await api<CreatorOnboardingProfile>("/creators/me");
        } catch (loadError) {
          if (!(loadError instanceof ApiError) || loadError.status !== 404) throw loadError;
          next = await api<CreatorOnboardingProfile>("/creators/me/application", {
            method: "POST",
          });
        }
        if (active) acceptProfile(next, true);
      } catch (loadError) {
        if (active) setError(creatorOnboardingError(loadError, "Unable to load creator application."));
      }
    }
    void load();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!profile || !["pending_verification", "pending_review"].includes(profile.status)) {
      return undefined;
    }
    let active = true;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "hidden" || actionLock.current) return;
      void api<CreatorOnboardingProfile>("/creators/me")
        .then((next) => {
          if (active) setProfile(next);
        })
        .catch(() => undefined);
    }, 4_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [profile?.status]);

  async function persistProfile(form: HTMLFormElement): Promise<CreatorOnboardingProfile> {
    if (!profile) throw new Error("Creator application is still loading.");
    const payload = creatorProfilePayload(
      new FormData(form),
      profile.status === "approved" && creatorHasCurrentVerification(profile),
    );
    return api<CreatorOnboardingProfile>("/creators/me", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (actionLock.current) return;
    actionLock.current = true;
    setPendingAction("save");
    setError("");
    setNotice("");
    try {
      const saved = await persistProfile(event.currentTarget);
      acceptProfile(saved, true);
      setNotice("Profile changes saved.");
    } catch (saveError) {
      setError(creatorOnboardingError(saveError, "Unable to save the creator profile."));
    } finally {
      actionLock.current = false;
      setPendingAction(null);
    }
  }

  async function submit() {
    if (!profile || !formRef.current || actionLock.current) return;
    if (!formRef.current.reportValidity()) return;
    actionLock.current = true;
    setPendingAction("submit");
    setError("");
    setNotice("");
    try {
      await persistProfile(formRef.current);
      const submitted = await api<CreatorOnboardingProfile>("/creators/me/submit", {
        method: "POST",
      });
      acceptProfile(submitted, true);
      setNotice("Application saved and submitted for identity verification.");
    } catch (submitError) {
      setError(creatorOnboardingError(submitError, "Unable to submit the creator application."));
    } finally {
      actionLock.current = false;
      setPendingAction(null);
    }
  }

  async function verify() {
    if (!profile || !canRunDevelopmentVerification(profile) || actionLock.current) return;
    actionLock.current = true;
    setPendingAction("verify");
    setError("");
    setNotice("");
    try {
      const next = await api<CreatorOnboardingProfile>(
        "/creators/me/verification/development",
        { method: "POST" },
      );
      acceptProfile(next);
      setNotice("Development verification recorded. The application is now pending review.");
    } catch (verificationError) {
      setError(creatorOnboardingError(verificationError, "Unable to continue identity verification."));
    } finally {
      actionLock.current = false;
      setPendingAction(null);
    }
  }

  if (!profile) {
    return (
      <section aria-busy={!error} className={`card ${styles.shell}`}>
        <p className="eyebrow">CREATOR ONBOARDING</p>
        <h1>Build your creator profile</h1>
        {error
          ? <p className={styles.alertError} role="alert">{error}</p>
          : <p>Loading your saved application…</p>}
      </section>
    );
  }

  const guidance = statusCopy(profile);
  const selectedCategories = new Set(profile.categories.map((item) => item.code));
  const selectedLanguages = new Set(profile.languages.map((item) => item.code));
  const currentVerification = creatorHasCurrentVerification(profile);
  const busy = pendingAction !== null;

  return (
    <section aria-labelledby="creator-onboarding-heading" className={`card ${styles.shell}`}>
      <header className={styles.header}>
        <div>
          <p className="eyebrow">CREATOR ONBOARDING</p>
          <h1 id="creator-onboarding-heading">Build your creator profile</h1>
          <p>Profile setup, identity review, and publication stay separate so each decision is explicit.</p>
        </div>
        <div className={styles.status}>
          <span>Application status</span>
          <strong>{profile.status.replaceAll("_", " ")}</strong>
        </div>
      </header>

      <div className={styles.layout}>
        <aside className={styles.guidance} aria-labelledby="application-next-step">
          <p className={styles.stepLabel}>CURRENT STEP</p>
          <h2 id="application-next-step">{guidance.heading}</h2>
          <p>{guidance.body}</p>
          <ol aria-label="Creator application stages" className={styles.stageList}>
            <li className={profile.status === "draft" ? styles.currentStage : undefined}>Profile draft</li>
            <li className={profile.status === "pending_verification" ? styles.currentStage : undefined}>Identity verification</li>
            <li className={profile.status === "pending_review" ? styles.currentStage : undefined}>Moderation review</li>
            <li className={profile.status === "approved" ? styles.currentStage : undefined}>Approved and publication choice</li>
          </ol>
          {profile.adult_verified && (
            <p className={styles.verifiedNote}>A current creator adult-KYC outcome is recorded. Fan adult access remains a separate check.</p>
          )}
          <p className={styles.mediaBoundary}>
            Avatar and cover editing will appear only after the media domain exposes an owner-authorised profile association command.
          </p>
        </aside>

        <div className={styles.editor}>
          <form aria-busy={busy} onSubmit={save} ref={formRef}>
            <section aria-labelledby="creator-identity-fields" className={styles.formSection}>
              <div className={styles.sectionHeading}>
                <span>01</span>
                <div>
                  <h2 id="creator-identity-fields">Public identity</h2>
                  <p>Your username and display name are required before submission.</p>
                </div>
              </div>
              <div className={styles.twoColumns}>
                <label>
                  Username
                  <input
                    autoComplete="off"
                    defaultValue={profile.username ?? ""}
                    disabled={busy}
                    maxLength={32}
                    name="username"
                    pattern="[a-zA-Z][a-zA-Z0-9_-]{2,31}"
                    required
                  />
                  <small>3–32 characters. Letters, numbers, underscores, and hyphens.</small>
                </label>
                <label>
                  Display name
                  <input defaultValue={profile.display_name ?? ""} disabled={busy} maxLength={80} name="display_name" required />
                </label>
              </div>
              <label>
                Bio
                <textarea defaultValue={profile.bio ?? ""} disabled={busy} maxLength={2000} name="bio" placeholder="Tell fans what you create and when to find you." />
              </label>
            </section>

            <section aria-labelledby="creator-location-fields" className={styles.formSection}>
              <div className={styles.sectionHeading}>
                <span>02</span>
                <div>
                  <h2 id="creator-location-fields">Location and timezone</h2>
                  <p>Location is private unless you explicitly choose to show it.</p>
                </div>
              </div>
              <div className={styles.locationGrid}>
                <label>
                  Country code
                  <input defaultValue={profile.country_code ?? ""} disabled={busy} maxLength={2} name="country_code" pattern="[A-Za-z]{2}" placeholder="PT" />
                </label>
                <label>Region<input defaultValue={profile.region ?? ""} disabled={busy} maxLength={80} name="region" /></label>
                <label>City<input defaultValue={profile.city ?? ""} disabled={busy} maxLength={80} name="city" /></label>
                <label>
                  Timezone
                  <input defaultValue={profile.timezone ?? ""} disabled={busy} maxLength={64} name="timezone" placeholder="Europe/Lisbon" />
                </label>
              </div>
              <label className={styles.checkLine}>
                <input defaultChecked={profile.show_location} disabled={busy} name="show_location" type="checkbox" />
                Show my configured city, region, and country on the public profile
              </label>
            </section>

            <section aria-labelledby="creator-discovery-fields" className={styles.formSection}>
              <div className={styles.sectionHeading}>
                <span>03</span>
                <div>
                  <h2 id="creator-discovery-fields">Discovery details</h2>
                  <p>Only enabled platform categories and languages can be saved.</p>
                </div>
              </div>
              <div className={styles.taxonomyGrid}>
                <fieldset>
                  <legend>Categories</legend>
                  {profile.available_categories.length ? profile.available_categories.map((item) => (
                    <label className={styles.checkLine} key={item.id}>
                      <input defaultChecked={selectedCategories.has(item.code)} disabled={busy} name="category_slugs" type="checkbox" value={item.code} />
                      {item.label}
                    </label>
                  )) : <p className={styles.empty}>No creator categories are currently enabled.</p>}
                </fieldset>
                <fieldset>
                  <legend>Languages</legend>
                  {profile.available_languages.length ? profile.available_languages.map((item) => (
                    <label className={styles.checkLine} key={item.id}>
                      <input defaultChecked={selectedLanguages.has(item.code)} disabled={busy} name="language_codes" type="checkbox" value={item.code} />
                      {item.label}
                    </label>
                  )) : <p className={styles.empty}>No creator languages are currently enabled.</p>}
                </fieldset>
              </div>
            </section>

            <section aria-labelledby="creator-social-fields" className={styles.formSection}>
              <div className={styles.sectionHeading}>
                <span>04</span>
                <div>
                  <h2 id="creator-social-fields">Social links</h2>
                  <p>Add up to 12 labelled web links. Blank rows are ignored.</p>
                </div>
              </div>
              <div className={styles.linkList}>
                {socialLinks.map((link, index) => (
                  <fieldset key={index}>
                    <legend>Link {index + 1}</legend>
                    <div className={styles.linkRow}>
                      <label>
                        Label
                        <input disabled={busy} maxLength={48} name="social_label" onChange={(event) => setSocialLinks((current) => current.map((item, position) => position === index ? { ...item, label: event.target.value } : item))} placeholder="Portfolio" value={link.label} />
                      </label>
                      <label>
                        URL
                        <input disabled={busy} maxLength={512} name="social_url" onChange={(event) => setSocialLinks((current) => current.map((item, position) => position === index ? { ...item, url: event.target.value } : item))} placeholder="https://example.com/your-profile" type="url" value={link.url} />
                      </label>
                      <button className={styles.removeLink} disabled={busy} onClick={() => setSocialLinks((current) => {
                        const next = current.filter((_, position) => position !== index);
                        return next.length ? next : [{ label: "", url: "" }];
                      })} type="button">
                        Remove link {index + 1}
                      </button>
                    </div>
                  </fieldset>
                ))}
              </div>
              <button className={styles.secondaryButton} disabled={busy || socialLinks.length >= 12} onClick={() => setSocialLinks((current) => [...current, { label: "", url: "" }])} type="button">
                Add another link
              </button>
            </section>

            {profile.status === "approved" && (
              <fieldset className={styles.publishChoice}>
                <legend>Publication</legend>
                <label className={styles.checkLine}>
                  <input defaultChecked={profile.is_public} disabled={busy || !currentVerification} name="is_public" type="checkbox" />
                  Make my approved creator profile public
                </label>
                <p>{currentVerification
                  ? "Approval and publication are separate. Turning this off removes the profile from public serving."
                  : "Publication changes are unavailable until creator verification is current again."}</p>
              </fieldset>
            )}

            <div className={styles.actions}>
              <button disabled={busy} type="submit">{pendingAction === "save" ? "Saving…" : "Save profile"}</button>
              {profile.status === "draft" && (
                <button className={styles.secondaryButton} disabled={busy} onClick={submit} type="button">
                  {pendingAction === "submit" ? "Submitting…" : "Submit application"}
                </button>
              )}
              {canRunDevelopmentVerification(profile) && (
                <button className={styles.secondaryButton} disabled={busy} onClick={verify} type="button">
                  {pendingAction === "verify" ? "Recording…" : "Complete development verification"}
                </button>
              )}
              {profile.status === "approved" && currentVerification && profile.is_public && profile.username && (
                <Link className="button" href={`/creator/${encodeURIComponent(profile.username)}`}>View public profile</Link>
              )}
            </div>
          </form>

          <div aria-live="polite" className={styles.feedback}>
            {notice && <p className={styles.alertSuccess} role="status">{notice}</p>}
            {error && <p className={styles.alertError} role="alert">{error}</p>}
          </div>
        </div>
      </div>
    </section>
  );
}
