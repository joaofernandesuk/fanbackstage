"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError } from "../lib/api";
import {
  creatorComplianceIsCurrent,
  type CreatorComplianceProjection,
} from "../lib/creator-compliance";
import { getComplianceCountries, type ComplianceCountry } from "../lib/compliance-api";
import {
  defaultTimezoneForCountry,
  regionForName,
  regionsForCountry,
  type ProfileLocationRegion,
} from "../lib/profile-location-catalog";
import styles from "./creator-onboarding.module.css";

type TaxonomyItem = { id: string; code: string; label: string };
type SocialLink = { label: string; url: string };

const CATEGORY_EXPLANATIONS: Record<string, string> = {
  "solo-performances": "Solo-led content, shows, and personal performances.",
  "couples-collaborations": "Partnered content, guest appearances, and collaborations.",
  "glamour-lingerie": "Glamour, lingerie, styling, and sensual photo sets.",
  "fetish-kink": "Consensual fetish and kink-focused work, within platform rules.",
  "cosplay-fantasy": "Character, cosplay, fantasy, and themed concepts.",
  "live-shows": "Interactive live shows, streams, and real-time sessions.",
  "photo-sets": "Curated photography, portraits, and visual collections.",
  "video-behind-scenes": "Videos, studio diaries, and behind-the-scenes access.",
  "audio-asmr": "Voice, audio, ASMR, and sound-led experiences.",
  "fitness-body-confidence": "Fitness, movement, body confidence, and wellness-led content.",
  "roleplay-characters": "Roleplay, personas, characters, and scripted scenarios.",
  "custom-content": "Custom requests, personal drops, and fan-led commissions.",
};

const DISCOVERY_INTEREST_CODES = new Set(Object.keys(CATEGORY_EXPLANATIONS));

function categoryExplanation(item: TaxonomyItem): string {
  return CATEGORY_EXPLANATIONS[item.code.toLowerCase()]
    ?? "A public discovery tag that helps the right fans find your work.";
}

export type CreatorOnboardingProfile = CreatorComplianceProjection & {
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
  rejection_reason: string | null;
  languages: TaxonomyItem[];
  categories: TaxonomyItem[];
  social_links: SocialLink[];
  available_languages: TaxonomyItem[];
  available_categories: TaxonomyItem[];
  development_verification_available: boolean;
  staging_kyc_sandbox_available: boolean;
  staging_kyc_session_reference: string | null;
  staging_kyc_verification_id: string | null;
};

type PendingAction = "save" | "submit" | "verify" | null;
type UsernameAvailability = "idle" | "checking" | "available" | "unavailable" | "error";

export function canRunDevelopmentVerification(profile: CreatorOnboardingProfile): boolean {
  return profile.status === "pending_verification"
    && profile.development_verification_available;
}

export function canRunStagingKyc(profile: CreatorOnboardingProfile): boolean {
  return profile.status === "pending_verification" && profile.staging_kyc_sandbox_available;
}

export function creatorHasCurrentVerification(profile: CreatorOnboardingProfile): boolean {
  return creatorComplianceIsCurrent(profile);
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
  const countryDisplay = String(form.get("country_display") ?? "").trim();
  if (countryDisplay && !countryCode) {
    throw new Error("Choose a country from the suggestions.");
  }
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
        : canRunStagingKyc(profile)
          ? {
            heading: "Identity verification is next",
            body: "Complete the staging sandbox identity check. Provider confirmation is asynchronous.",
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
          body: `${profile.creator_compliance.reason}. Saved profile details and your publication preference remain intact.`,
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
  "Choose a country from the suggestions.",
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
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [usernameAvailability, setUsernameAvailability] = useState<UsernameAvailability>("idle");
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [countries, setCountries] = useState<ComplianceCountry[]>([]);
  const [countryCode, setCountryCode] = useState("");
  const [countryDisplay, setCountryDisplay] = useState("");
  const [countryPickerOpen, setCountryPickerOpen] = useState(false);
  const [regionPickerOpen, setRegionPickerOpen] = useState(false);
  const [cityPickerOpen, setCityPickerOpen] = useState(false);
  const [region, setRegion] = useState("");
  const [city, setCity] = useState("");
  const [timezone, setTimezone] = useState("");
  const [selectedCategoryCodes, setSelectedCategoryCodes] = useState<string[]>([]);
  const [interestSearch, setInterestSearch] = useState("");
  const [selectedLanguageCodes, setSelectedLanguageCodes] = useState<string[]>([]);
  const [languageSearch, setLanguageSearch] = useState("");

  const selectedCountryName = (code: string | null | undefined, available = countries) => (
    available.find((country) => country.code === code)?.name ?? code ?? ""
  );

  function acceptProfile(next: CreatorOnboardingProfile, resetLinks = false) {
    setProfile(next);
    if (resetLinks) {
      setSocialLinks(next.social_links.length ? next.social_links : [{ label: "", url: "" }]);
      // Older profiles can retain retired generic taxonomy values. Do not send
      // those values back with an otherwise-valid profile update: the current
      // catalogue is authoritative and the user can choose from it below.
      setSelectedCategoryCodes(
        next.categories
          .map((item) => item.code)
          .filter((code) => DISCOVERY_INTEREST_CODES.has(code)),
      );
      setSelectedLanguageCodes(next.languages.map((item) => item.code));
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
    void getComplianceCountries()
      .then((available) => {
        if (active) setCountries(available);
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!profile) return;
    setUsername(profile.username ?? "");
    setDisplayName(profile.display_name ?? "");
    setCountryCode(profile.country_code ?? "");
    setCountryDisplay(selectedCountryName(profile.country_code));
    setRegion(profile.region ?? "");
    setCity(profile.city ?? "");
    setTimezone(profile.timezone ?? defaultTimezoneForCountry(profile.country_code ?? "", browserTimezone()));
  }, [countries, profile?.city, profile?.country_code, profile?.display_name, profile?.region, profile?.timezone, profile?.username]);

  const countryMatches = useMemo(() => {
    const query = countryDisplay.trim().toLocaleLowerCase();
    if (!query) return countries.slice(0, 8);
    return countries.filter((country) => (
      country.name.toLocaleLowerCase().includes(query)
      || country.code.toLocaleLowerCase().startsWith(query)
    )).slice(0, 8);
  }, [countries, countryDisplay]);

  const regionMatches = useMemo(() => {
    const query = region.trim().toLocaleLowerCase();
    const available = regionsForCountry(countryCode);
    if (!query) return available.slice(0, 10);
    return available.filter((item) => item.name.toLocaleLowerCase().includes(query)).slice(0, 10);
  }, [countryCode, region]);

  const selectedRegion = regionForName(countryCode, region);
  const cityMatches = useMemo(() => {
    if (!selectedRegion) return [];
    const query = city.trim().toLocaleLowerCase();
    if (!query) return selectedRegion.cities.slice(0, 12);
    return selectedRegion.cities.filter((item) => item.toLocaleLowerCase().includes(query)).slice(0, 12);
  }, [city, selectedRegion]);

  function browserTimezone(): string {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch {
      return "";
    }
  }

  function selectCountry(country: ComplianceCountry) {
    if (country.code !== countryCode) {
      setRegion("");
      setCity("");
    }
    setCountryCode(country.code);
    setCountryDisplay(country.name);
    setTimezone(defaultTimezoneForCountry(country.code, browserTimezone()));
    setCountryPickerOpen(false);
    setRegionPickerOpen(false);
    setCityPickerOpen(false);
  }

  function selectRegion(next: ProfileLocationRegion) {
    setRegion(next.name);
    setCity("");
    setTimezone(next.timezone || defaultTimezoneForCountry(countryCode, browserTimezone()));
    setRegionPickerOpen(false);
    setCityPickerOpen(false);
  }

  function selectCity(next: string) {
    setCity(next);
    setCityPickerOpen(false);
  }

  useEffect(() => {
    const candidate = username.trim().toLowerCase();
    if (!profile || !candidate) {
      setUsernameAvailability("idle");
      return undefined;
    }
    if (!/^[a-z][a-z0-9_-]{2,31}$/.test(candidate)) {
      setUsernameAvailability("unavailable");
      return undefined;
    }

    let active = true;
    setUsernameAvailability("checking");
    const timer = window.setTimeout(() => {
      void api<{ username: string; available: boolean }>(
        `/creators/me/username-availability?username=${encodeURIComponent(candidate)}`,
      ).then((result) => {
        if (active) setUsernameAvailability(result.available ? "available" : "unavailable");
      }).catch(() => {
        if (active) setUsernameAvailability("error");
      });
    }, 350);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [Boolean(profile), username]);

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

  async function startStagingKyc() {
    if (!profile || !canRunStagingKyc(profile) || actionLock.current) return;
    actionLock.current = true;
    setPendingAction("verify");
    setError("");
    setNotice("");
    try {
      const next = await api<CreatorOnboardingProfile>(
        "/creators/me/verification/staging-sandbox/start",
        { method: "POST" },
      );
      acceptProfile(next);
      setNotice("Sandbox identity verification started. Provider confirmation is pending.");
    } catch (verificationError) {
      setError(creatorOnboardingError(verificationError, "Unable to start identity verification."));
    } finally {
      actionLock.current = false;
      setPendingAction(null);
    }
  }

  async function completeStagingKyc() {
    if (!profile?.staging_kyc_verification_id || actionLock.current) return;
    actionLock.current = true;
    setPendingAction("verify");
    setError("");
    setNotice("");
    try {
      await api(`/creators/me/verification/staging-sandbox/${profile.staging_kyc_verification_id}/complete`, {
        method: "POST",
        body: JSON.stringify({ outcome: "VERIFIED" }),
      });
      setNotice("Sandbox identity result queued. The authoritative provider callback is pending.");
    } catch (verificationError) {
      setError(creatorOnboardingError(verificationError, "Unable to complete staging identity verification."));
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
  const currentVerification = creatorHasCurrentVerification(profile);
  const busy = pendingAction !== null;
  const availableInterests = profile.available_categories.filter((item) => DISCOVERY_INTEREST_CODES.has(item.code));
  const matchingInterests = availableInterests.filter((item) => (
    item.label.toLocaleLowerCase().includes(interestSearch.trim().toLocaleLowerCase())
    || item.code.includes(interestSearch.trim().toLocaleLowerCase())
  ));
  const selectedInterests = availableInterests.filter((item) => selectedCategoryCodes.includes(item.code));
  const availableLanguages = profile.available_languages;
  const matchingLanguages = availableLanguages.filter((item) => (
    item.label.toLocaleLowerCase().includes(languageSearch.trim().toLocaleLowerCase())
    || item.code.includes(languageSearch.trim().toLocaleLowerCase())
  ));
  const selectedLanguageItems = availableLanguages.filter((item) => selectedLanguageCodes.includes(item.code));

  function toggleInterest(code: string) {
    setSelectedCategoryCodes((current) => (
      current.includes(code) ? current.filter((item) => item !== code) : [...current, code]
    ));
  }

  function toggleLanguage(code: string) {
    setSelectedLanguageCodes((current) => {
      if (current.includes(code)) return current.filter((item) => item !== code);
      return current.length >= 10 ? current : [...current, code];
    });
  }

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
          <p className={currentVerification ? styles.verifiedNote : styles.mediaBoundary}>
            Current creator policy: {currentVerification
              ? "identity and age requirements are satisfied"
              : profile.creator_compliance.reason}. Latest provider evidence: {profile.verification_status.replaceAll("_", " ")}.
            Fan adult access remains a separate check.
          </p>
          <p className={styles.mediaBoundary}>
            After approval, manage avatar and cover images from the Creator Studio media library.
          </p>
          {canRunDevelopmentVerification(profile) && (
            <div className={styles.nextAction}>
              <strong>Ready to continue?</strong>
              <p>This is a local development-only identity check. It records a safe test result; it is never available in production.</p>
              <button className={styles.submitButton} disabled={busy} onClick={verify} type="button">
                {pendingAction === "verify" ? "Recording…" : "Complete identity check"}
              </button>
            </div>
          )}
          {canRunStagingKyc(profile) && (
            <div className={styles.nextAction}>
              <strong>Ready to continue?</strong>
              <p>Start the staging KYC session. Its signed provider-style callback will place the application into the admin review queue.</p>
              <button className={styles.submitButton} disabled={busy} onClick={startStagingKyc} type="button">
                {pendingAction === "verify" ? "Starting…" : "Start identity check"}
              </button>
            </div>
          )}
          {profile.staging_kyc_verification_id && (
            <div className={styles.nextAction}>
              <strong>Provider confirmation pending</strong>
              <p>For the local staging sandbox, you can submit the signed test outcome now. Production providers complete this outside FanBackstage.</p>
              <button className={styles.secondaryButton} disabled={busy} onClick={completeStagingKyc} type="button">
                {pendingAction === "verify" ? "Submitting…" : "Complete sandbox identity check"}
              </button>
            </div>
          )}
        </aside>

        <div className={styles.editor}>
          <form aria-busy={busy} onSubmit={save} ref={formRef}>
            <section aria-labelledby="creator-identity-fields" className={styles.formSection}>
              <div className={styles.sectionHeading}>
                <span>01</span>
                <div>
                  <h2 id="creator-identity-fields">Public identity</h2>
                  <p>Choose the name and handle fans will recognise across your profile, posts, Stories, and live rooms.</p>
                </div>
              </div>
              <div className={styles.identityIntro}>
                <div>
                  <strong>Make it recognisable</strong>
                  <span>Your display name is your headline. Your @handle is your unique profile address and cannot contain spaces.</span>
                </div>
                <div className={styles.identityPreview} aria-label="Live public profile preview">
                  <span className={styles.previewAvatar}>{(displayName || username || "F").slice(0, 1).toUpperCase()}</span>
                  <div>
                    <small>Fan preview</small>
                    <strong>{displayName || "Your creator name"}</strong>
                    <span>@{username || "your-handle"}</span>
                  </div>
                </div>
              </div>
              <div className={styles.twoColumns}>
                <label>
                  Your @handle
                  <input
                    aria-describedby="creator-handle-help"
                    autoComplete="off"
                    disabled={busy}
                    maxLength={32}
                    name="username"
                    onChange={(event) => setUsername(
                      event.target.value.toLowerCase().replace(/\s+/g, "").replace(/[^a-z0-9_-]/g, ""),
                    )}
                    pattern="[a-zA-Z][a-zA-Z0-9_-]{2,31}"
                    placeholder="for example, mercyafterdark"
                    required
                    value={username}
                  />
                  <small id="creator-handle-help">Your unique profile address. Spaces are removed automatically; use 3–32 letters, numbers, underscores, or hyphens.</small>
                  {usernameAvailability === "checking" && <span className={styles.handleChecking}>Checking handle availability…</span>}
                  {usernameAvailability === "available" && <span className={styles.handleAvailable}>@{username} is available.</span>}
                  {usernameAvailability === "unavailable" && <span className={styles.handleUnavailable}>Choose a different handle—this one is unavailable or invalid.</span>}
                  {usernameAvailability === "error" && <span className={styles.handleChecking}>We’ll check this again when you save.</span>}
                </label>
                <label>
                  Display name
                  <input
                    autoComplete="name"
                    disabled={busy}
                    maxLength={80}
                    name="display_name"
                    onChange={(event) => setDisplayName(event.target.value)}
                    placeholder="The name fans see first"
                    required
                    value={displayName}
                  />
                  <small>Use the creator name, stage name, or brand you want fans to see.</small>
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
              <div className={styles.locationSetup}>
                <label className={styles.countryPicker}>
                  <span>Country or territory</span>
                  <input
                    aria-controls="creator-country-options"
                    aria-describedby="creator-country-help"
                    aria-expanded={countryPickerOpen}
                    autoComplete="country-name"
                    disabled={busy}
                    name="country_display"
                    onBlur={() => window.setTimeout(() => setCountryPickerOpen(false), 140)}
                    onChange={(event) => {
                      const value = event.target.value;
                      const match = countries.find((country) => (
                        country.name.toLocaleLowerCase() === value.trim().toLocaleLowerCase()
                        || country.code === value.trim().toUpperCase()
                      ));
                      setCountryDisplay(value);
                      if (match?.code !== countryCode) {
                        setRegion("");
                        setCity("");
                      }
                      setCountryCode(match?.code ?? "");
                      if (match) setTimezone(defaultTimezoneForCountry(match.code, browserTimezone()));
                      setCountryPickerOpen(true);
                    }}
                    onFocus={() => setCountryPickerOpen(true)}
                    placeholder="Search by country name or two-letter code"
                    role="combobox"
                    value={countryDisplay}
                  />
                  <input name="country_code" type="hidden" value={countryCode} />
                  {countryPickerOpen && countryMatches.length > 0 && (
                    <div className={styles.countryOptions} id="creator-country-options" role="listbox">
                      {countryMatches.map((country) => (
                        <button
                          aria-selected={country.code === countryCode}
                          key={country.code}
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => selectCountry(country)}
                          role="option"
                          type="button"
                        >
                          <span>{country.name}</span><small>{country.code}</small>
                        </button>
                      ))}
                    </div>
                  )}
                  <small id="creator-country-help">Choose the country you want to show on your profile. It does not change your verified account jurisdiction.</small>
                </label>
                <div className={styles.locationAutoFields} aria-label="Location details set from your country">
                  <label className={styles.dependentPicker}>
                    <span>Region</span>
                    <input
                      aria-controls="creator-region-options"
                      aria-expanded={regionPickerOpen}
                      autoComplete="address-level1"
                      disabled={busy || !countryCode}
                      onBlur={() => window.setTimeout(() => setRegionPickerOpen(false), 140)}
                      onChange={(event) => {
                        const value = event.target.value;
                        const exact = regionForName(countryCode, value);
                        setRegion(value);
                        setCity("");
                        if (exact) setTimezone(exact.timezone);
                        else setTimezone(defaultTimezoneForCountry(countryCode, browserTimezone()));
                        setRegionPickerOpen(true);
                      }}
                      onFocus={() => setRegionPickerOpen(true)}
                      placeholder={countryCode ? "Search region or state" : "Choose a country first"}
                      role="combobox"
                      value={region}
                    />
                    {regionPickerOpen && countryCode && regionMatches.length > 0 && (
                      <div className={styles.dependentOptions} id="creator-region-options" role="listbox">
                        {regionMatches.map((item) => (
                          <button
                            aria-selected={item.name === region}
                            key={item.name}
                            onClick={() => selectRegion(item)}
                            onMouseDown={(event) => event.preventDefault()}
                            role="option"
                            type="button"
                          >
                            {item.name}
                          </button>
                        ))}
                      </div>
                    )}
                    {countryCode && regionPickerOpen && !regionMatches.length && (
                      <small className={styles.pickerHint}>No matching region. You can keep the region you typed.</small>
                    )}
                  </label>
                  <label className={styles.dependentPicker}>
                    <span>City</span>
                    <input
                      aria-controls="creator-city-options"
                      aria-expanded={cityPickerOpen}
                      autoComplete="address-level2"
                      disabled={busy || !countryCode || !region}
                      onBlur={() => window.setTimeout(() => setCityPickerOpen(false), 140)}
                      onChange={(event) => {
                        setCity(event.target.value);
                        setCityPickerOpen(true);
                      }}
                      onFocus={() => setCityPickerOpen(true)}
                      placeholder={region ? "Search or enter your city" : "Choose a region first"}
                      role="combobox"
                      value={city}
                    />
                    {cityPickerOpen && region && cityMatches.length > 0 && (
                      <div className={styles.dependentOptions} id="creator-city-options" role="listbox">
                        {cityMatches.map((item) => (
                          <button
                            aria-selected={item === city}
                            key={item}
                            onClick={() => selectCity(item)}
                            onMouseDown={(event) => event.preventDefault()}
                            role="option"
                            type="button"
                          >
                            {item}
                          </button>
                        ))}
                      </div>
                    )}
                    {region && cityPickerOpen && !cityMatches.length && (
                      <small className={styles.pickerHint}>Your city is not in the suggestions yet. You can enter it yourself.</small>
                    )}
                  </label>
                  <label>
                    Timezone
                    <input disabled readOnly value={timezone || "Choose a country first"} />
                  </label>
                </div>
                <input name="region" type="hidden" value={region} />
                <input name="city" type="hidden" value={city} />
                <input name="timezone" type="hidden" value={timezone} />
                <p className={styles.locationNote}>Choose a country, then a region and city. The time zone updates from the selected region. Your location stays private unless you opt in below.</p>
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
                  <h2 id="creator-discovery-fields">Help fans find your work</h2>
                  <p>Add the interests that best describe your public work, then choose the languages you create in.</p>
                </div>
              </div>
              <aside className={styles.discoveryNote}>
                <strong>These are discovery interests—not account roles.</strong>
                <span>They never change your permissions, consent requirements, or content rules. Choose only what genuinely describes your public work; you can update them later.</span>
              </aside>
              <div className={styles.taxonomyGrid}>
                <fieldset>
                  <legend>Your creator interests</legend>
                  <div className={styles.interestPicker}>
                    <label className={styles.interestSearch}>
                      <span className="sr-only">Search creator interests</span>
                      <input
                        disabled={busy || !availableInterests.length}
                        onChange={(event) => setInterestSearch(event.target.value)}
                        placeholder="Search interests, for example live, cosplay, photo sets…"
                        type="search"
                        value={interestSearch}
                      />
                    </label>
                    {selectedInterests.length > 0 && (
                      <div aria-label="Selected creator interests" className={styles.interestChips}>
                        {selectedInterests.map((item) => (
                          <button disabled={busy} key={item.id} onClick={() => toggleInterest(item.code)} type="button">
                            {item.label}<span aria-hidden="true">×</span>
                          </button>
                        ))}
                      </div>
                    )}
                    <div aria-label="Available creator interests" className={styles.interestOptions}>
                      {matchingInterests.map((item) => {
                        const selected = selectedCategoryCodes.includes(item.code);
                        return (
                          <button
                            aria-pressed={selected}
                            className={selected ? styles.interestSelected : undefined}
                            disabled={busy}
                            key={item.id}
                            onClick={() => toggleInterest(item.code)}
                            type="button"
                          >
                            <span>{item.label}</span>
                            <small>{categoryExplanation(item)}</small>
                          </button>
                        );
                      })}
                    </div>
                    {!availableInterests.length && <p className={styles.empty}>Creator interests are being prepared. Please refresh shortly.</p>}
                    {availableInterests.length > 0 && !matchingInterests.length && <p className={styles.empty}>No interests match that search.</p>}
                    {selectedCategoryCodes.map((code) => <input key={code} name="category_slugs" type="hidden" value={code} />)}
                  </div>
                </fieldset>
                <fieldset>
                  <legend>Languages you create in</legend>
                  <div className={styles.interestPicker}>
                    <label className={styles.interestSearch}>
                      <span className="sr-only">Search languages</span>
                      <input
                        disabled={busy || !availableLanguages.length}
                        onChange={(event) => setLanguageSearch(event.target.value)}
                        placeholder="Search languages, for example Portuguese, Arabic, Korean…"
                        type="search"
                        value={languageSearch}
                      />
                    </label>
                    {selectedLanguageItems.length > 0 && (
                      <div aria-label="Selected creator languages" className={styles.interestChips}>
                        {selectedLanguageItems.map((item) => (
                          <button disabled={busy} key={item.id} onClick={() => toggleLanguage(item.code)} type="button">
                            {item.label}<span aria-hidden="true">×</span>
                          </button>
                        ))}
                      </div>
                    )}
                    <div aria-label="Available creator languages" className={styles.languageOptions}>
                      {matchingLanguages.map((item) => {
                        const selected = selectedLanguageCodes.includes(item.code);
                        return (
                          <button
                            aria-pressed={selected}
                            className={selected ? styles.languageSelected : undefined}
                            disabled={busy}
                            key={item.id}
                            onClick={() => toggleLanguage(item.code)}
                            type="button"
                          >
                            <span>{item.label}</span>
                            <small>{item.code.toUpperCase()}</small>
                          </button>
                        );
                      })}
                    </div>
                    {!availableLanguages.length && <p className={styles.empty}>Creator languages are being prepared. Please refresh shortly.</p>}
                    {availableLanguages.length > 0 && !matchingLanguages.length && <p className={styles.empty}>No languages match that search.</p>}
                    <p className={styles.selectionHelp}>Choose up to 10 languages. Select again to remove one.</p>
                    {selectedLanguageCodes.map((code) => <input key={code} name="language_codes" type="hidden" value={code} />)}
                  </div>
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
              <fieldset className={styles.publishChoice} id="publication">
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
              <button className={styles.saveButton} disabled={busy} type="submit">{pendingAction === "save" ? "Saving…" : "Save profile"}</button>
              {profile.status === "draft" && (
                <button className={styles.submitButton} disabled={busy} onClick={submit} type="button">
                  {pendingAction === "submit" ? "Submitting…" : "Submit application"}
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
