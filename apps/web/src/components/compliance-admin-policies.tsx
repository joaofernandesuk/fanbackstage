"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../lib/api";
import {
  assuranceLevels,
  complianceFeatures,
  confirmedCompliancePayload,
  featureFlagWeakens,
  formatOperationalLabel,
  formatPolicyValue,
  localDateTimeValue,
  mergePolicyRules,
  optionalIso,
  policyRuleDefinitions,
  policyStatuses,
  policyWeakeningChanges,
  safeBlockedPolicyDraft,
  type ComplianceFeature,
  type CountryPolicy,
  type FeatureFlagRevision,
  type JurisdictionRevision,
  type PageResult,
  type PolicyOverrides,
  type PolicyRuleDefinition,
  type PolicyRuleGroup,
  type PolicyRuleKey,
  type PolicyRules,
  type PolicyTemplate,
  type TemplateRevision,
} from "../lib/compliance-admin";
import styles from "./compliance-admin.module.css";
import {
  EmptyState,
  FormMessage,
  LoadingState,
  Pagination,
  SectionHeader,
  StatusBadge,
  formatDateTime,
  operationalError,
} from "./compliance-admin-shared";

const PAGE_SIZE = 25;
const policyGroups: PolicyRuleGroup[] = ["Access", "Age assurance", "Creator", "Performers", "Provider"];

type TemplateRevisionOption = {
  template: PolicyTemplate;
  revision: TemplateRevision;
};

async function loadTemplateRevisionOptions(): Promise<{ options: TemplateRevisionOption[]; truncated: boolean }> {
  const templates = await api<PageResult<PolicyTemplate>>("/admin/compliance/templates?page=1&page_size=100");
  const revisions = await Promise.all(
    templates.items.map(async (template) => ({
      template,
      revisions: await api<TemplateRevision[]>(`/admin/compliance/templates/${template.id}/revisions`),
    })),
  );
  return {
    options: revisions.flatMap(({ template, revisions: templateRevisions }) =>
      templateRevisions.map((revision) => ({ template, revision }))),
    truncated: templates.total > templates.items.length,
  };
}

function updateRule(rules: PolicyRules, key: PolicyRuleKey, value: unknown): PolicyRules {
  return { ...rules, [key]: value } as PolicyRules;
}

function fieldControl(
  definition: PolicyRuleDefinition,
  value: PolicyRules[PolicyRuleKey],
  onChange: (value: unknown) => void,
) {
  if (definition.kind === "boolean") {
    return (
      <label className={styles.checkboxRow}>
        <input aria-label={definition.label} checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
        {value ? "On" : "Off"}
      </label>
    );
  }
  if (definition.kind === "assurance") {
    return (
      <select aria-label={definition.label} onChange={(event) => onChange(event.target.value)} value={String(value)}>
        {assuranceLevels.map((level) => <option key={level} value={level}>{formatOperationalLabel(level)}</option>)}
      </select>
    );
  }
  if (definition.kind === "number" || definition.kind === "nullable-number") {
    return (
      <input
        aria-label={definition.label}
        max={definition.maximum}
        min={definition.minimum}
        onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))}
        required={definition.kind === "number"}
        type="number"
        value={value === null ? "" : Number(value)}
      />
    );
  }
  return (
    <input
      aria-label={definition.label}
      maxLength={128}
      onChange={(event) => onChange(event.target.value === "" && definition.kind === "nullable-text" ? null : event.target.value)}
      required={definition.kind === "text"}
      type="text"
      value={value === null ? "" : String(value)}
    />
  );
}

function PolicyRuleEditor({
  rules,
  onRulesChange,
  overrides,
  onOverridesChange,
}: {
  rules: PolicyRules;
  onRulesChange?: (rules: PolicyRules) => void;
  overrides?: PolicyOverrides;
  onOverridesChange?: (overrides: PolicyOverrides) => void;
}) {
  const isOverrideMode = Boolean(overrides && onOverridesChange);

  function toggleOverride(key: PolicyRuleKey, checked: boolean) {
    if (!onOverridesChange || !overrides) return;
    const next = { ...overrides } as Record<string, unknown>;
    if (checked) next[key] = rules[key];
    else delete next[key];
    onOverridesChange(next as PolicyOverrides);
  }

  function changeValue(key: PolicyRuleKey, value: unknown) {
    if (isOverrideMode && onOverridesChange && overrides) {
      onOverridesChange({ ...overrides, [key]: value } as PolicyOverrides);
      return;
    }
    if (onRulesChange) onRulesChange(updateRule(rules, key, value));
  }

  return (
    <div className={styles.ruleGroups}>
      {policyGroups.map((group) => (
        <section className={styles.ruleGroup} key={group}>
          <h4>{group}</h4>
          <div className={styles.ruleGrid}>
            {policyRuleDefinitions.filter((definition) => definition.group === group).map((definition) => {
              const overridden = overrides ? Object.prototype.hasOwnProperty.call(overrides, definition.key) : false;
              return (
                <div className={`${styles.ruleCard} ${overridden ? styles.ruleCardOverridden : ""}`} key={definition.key}>
                  <div className={styles.ruleTitle}>
                    <strong>{definition.label}</strong>
                    {isOverrideMode ? (
                      <label className={styles.checkboxRow}>
                        <input aria-label={`Override ${definition.label}`} checked={overridden} onChange={(event) => toggleOverride(definition.key, event.target.checked)} type="checkbox" />
                        {overridden ? <span className={styles.overrideBadge}>Override</span> : <span className={styles.inheritedBadge}>Inherited</span>}
                      </label>
                    ) : null}
                  </div>
                  <p>{definition.description}</p>
                  {isOverrideMode && !overridden ? (
                    <strong>{formatPolicyValue(rules[definition.key])}</strong>
                  ) : fieldControl(definition, rules[definition.key], (value) => changeValue(definition.key, value))}
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

function WeakeningWarning({ before, after }: { before: PolicyRules | null; after: PolicyRules }) {
  if (!before) return <p className={styles.hint}>No prior revision could be resolved for an automated weakening comparison. Review every rule before confirming.</p>;
  const changes = policyWeakeningChanges(before, after);
  if (!changes.length) return <p className={styles.hint}>No known weakening pattern was detected. Confirmation and a durable reason are still required.</p>;
  return (
    <div>
      <h3>Potential policy weakening detected</h3>
      <p>The following changes expand access or reduce a configured assurance control:</p>
      <ul className={styles.warningList}>
        {changes.map((change) => <li key={change.field}>{change.label}: {formatPolicyValue(change.before)} → {formatPolicyValue(change.after)}</li>)}
      </ul>
    </div>
  );
}

export function ComplianceCountries() {
  const [data, setData] = useState<PageResult<CountryPolicy> | null>(null);
  const [page, setPage] = useState(1);
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (search) params.set("search", search);
    try {
      const next = await api<PageResult<CountryPolicy>>(`/admin/compliance/countries?${params}`);
      setData(next);
      if (selectedCode && !next.items.some((country) => country.code === selectedCode)) setSelectedCode(null);
    } catch (caught) {
      setError(operationalError(caught, "Unable to load country policies."));
    }
  }, [page, search, selectedCode]);

  useEffect(() => { void load(); }, [load]);

  function applySearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPage(1);
    setSearch(searchDraft.trim());
  }

  async function addCountry(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const form = new FormData(event.currentTarget);
    if (form.get("confirmed") !== "on") return;
    try {
      const created = await api<{ code: string; name: string; enabled: boolean }>("/admin/compliance/countries", {
        method: "POST",
        body: JSON.stringify(confirmedCompliancePayload({
          code: String(form.get("code") ?? "").trim().toUpperCase(),
          name: String(form.get("name") ?? "").trim(),
        }, String(form.get("change_reason") ?? ""))),
      });
      setMessage(`${created.name} (${created.code}) was added to the registry. Add a reviewed policy before relying on access.`);
      setShowAdd(false);
      setSearch("");
      setSearchDraft("");
      setPage(1);
      setSelectedCode(created.code);
      await load();
    } catch (caught) {
      setError(operationalError(caught, "Unable to add the country."));
    }
  }

  const selected = data?.items.find((country) => country.code === selectedCode) ?? null;

  return (
    <section className={styles.panel}>
      <SectionHeader
        action={<button className={styles.secondaryButton} onClick={() => setShowAdd((current) => !current)} type="button">{showAdd ? "Close form" : "Add ISO country"}</button>}
        description="Search the canonical registry, inspect effective policy beside inherited values, and create append-only country revisions."
        eyebrow="Jurisdictions"
        title="Countries and effective policy"
      />
      <FormMessage message={message} />
      <FormMessage error message={error} />
      {showAdd ? (
        <form className={styles.safeChange} onSubmit={addCountry}>
          <h3>Add canonical country</h3>
          <p>This creates a registry entry only. It does not make a legal claim or create an effective jurisdiction policy.</p>
          <div className={styles.formGrid}>
            <label>ISO alpha-2 code<input maxLength={2} minLength={2} name="code" placeholder="PT" required /></label>
            <label>Display name<input maxLength={120} name="name" required /></label>
          </div>
          <label>Operational reason<textarea minLength={8} name="change_reason" required /></label>
          <label className={styles.confirmation}><input name="confirmed" required type="checkbox" />I confirm this canonical registry change and understand that policy must be configured separately.</label>
          <div className={styles.formActions}><button type="submit">Add country</button></div>
        </form>
      ) : null}
      <form className={styles.filters} onSubmit={applySearch}>
        <label>Search countries<input onChange={(event) => setSearchDraft(event.target.value)} placeholder="Name or ISO code" value={searchDraft} /></label>
        <button className={styles.secondaryButton} type="submit">Search</button>
        {search ? <button className={styles.ghostButton} onClick={() => { setSearch(""); setSearchDraft(""); setPage(1); }} type="button">Clear</button> : <span />}
      </form>
      {!data && !error ? <LoadingState /> : null}
      {data ? (
        <>
          <div className={styles.countryWorkspace}>
            <div>
              {data.items.length ? (
                <ul className={styles.countryList}>
                  {data.items.map((country) => (
                    <li key={country.code}>
                      <button className={`${styles.countryButton} ${selectedCode === country.code ? styles.countryButtonSelected : ""}`} onClick={() => setSelectedCode(country.code)} type="button">
                        <span><strong>{country.name}</strong><br /><small>{country.code} · {country.effective_policy ? `policy v${country.effective_policy.version}` : "no effective policy"}</small></span>
                        <StatusBadge value={country.enabled && Boolean(country.effective_policy)} label={!country.enabled ? "Registry off" : country.effective_policy ? "Effective" : "Fail-closed"} />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : <EmptyState>No countries match this search.</EmptyState>}
              <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
            </div>
            {selected ? <CountryPolicyDetail country={selected} onChanged={() => void load()} /> : <EmptyState>Select a country to inspect its effective and inherited controls.</EmptyState>}
          </div>
        </>
      ) : null}
    </section>
  );
}

function CountryPolicyDetail({ country, onChanged }: { country: CountryPolicy; onChanged: () => void }) {
  const [jurisdictions, setJurisdictions] = useState<JurisdictionRevision[]>([]);
  const [options, setOptions] = useState<TemplateRevisionOption[]>([]);
  const [templatesTruncated, setTemplatesTruncated] = useState(false);
  const [selectedTemplateRevisionId, setSelectedTemplateRevisionId] = useState("");
  const [overrides, setOverrides] = useState<PolicyOverrides>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [availabilityReason, setAvailabilityReason] = useState("");
  const [availabilityConfirmed, setAvailabilityConfirmed] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [jurisdictionPage, templateData] = await Promise.all([
        api<PageResult<JurisdictionRevision>>(`/admin/compliance/jurisdictions?country_code=${encodeURIComponent(country.code)}&page=1&page_size=100`),
        loadTemplateRevisionOptions(),
      ]);
      setJurisdictions(jurisdictionPage.items);
      setOptions(templateData.options);
      setTemplatesTruncated(templateData.truncated);
      const source = jurisdictionPage.items.find((revision) => revision.id === country.effective_policy?.id) ?? jurisdictionPage.items[0];
      const preferredId = source?.template_revision_id ?? country.effective_policy?.template_revision_id ?? templateData.options[0]?.revision.id ?? "";
      setSelectedTemplateRevisionId(preferredId);
      setOverrides(source?.overrides ?? {});
    } catch (caught) {
      setError(operationalError(caught, "Unable to resolve this country policy."));
    } finally {
      setLoading(false);
    }
  }, [country.code, country.effective_policy?.id, country.effective_policy?.template_revision_id]);

  useEffect(() => { void load(); }, [load]);

  const selectedOption = options.find((option) => option.revision.id === selectedTemplateRevisionId) ?? null;
  const baseRules = selectedOption?.revision.rules ?? safeBlockedPolicyDraft;
  const effectiveRules = mergePolicyRules(baseRules, overrides);
  const currentSource = jurisdictions.find((revision) => revision.id === country.effective_policy?.id) ?? jurisdictions[0] ?? null;
  const currentOption = options.find((option) => option.revision.id === currentSource?.template_revision_id) ?? null;
  const currentRules = currentOption && currentSource ? mergePolicyRules(currentOption.revision.rules, currentSource.overrides) : null;

  async function saveRevision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedTemplateRevisionId || !confirmed || reason.trim().length < 8) return;
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const created = await api<{ id: string; version: number; status: string }>(`/admin/compliance/jurisdictions/${country.code}/revisions`, {
        method: "POST",
        body: JSON.stringify(confirmedCompliancePayload({
          template_revision_id: selectedTemplateRevisionId,
          overrides,
          status: form.get("status"),
          effective_from: new Date(String(form.get("effective_from"))).toISOString(),
          effective_until: optionalIso(form.get("effective_until")),
          reviewed: form.get("reviewed") === "on",
          is_demo: form.get("is_demo") === "on",
        }, reason)),
      });
      setMessage(`Created ${country.code} policy revision ${created.version} as ${created.status}.`);
      setConfirmed(false);
      setReason("");
      await load();
      onChanged();
    } catch (caught) {
      setError(operationalError(caught, "Unable to create the jurisdiction revision."));
    }
  }

  async function changeAvailability(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!availabilityConfirmed || availabilityReason.trim().length < 8) return;
    setError("");
    try {
      const updated = await api<{ code: string; enabled: boolean }>(`/admin/compliance/countries/${country.code}/availability`, {
        method: "PUT",
        body: JSON.stringify(confirmedCompliancePayload({
          enabled: !country.enabled,
        }, availabilityReason)),
      });
      setMessage(`${updated.code} registry availability is now ${updated.enabled ? "enabled" : "disabled"}.`);
      setAvailabilityReason("");
      setAvailabilityConfirmed(false);
      onChanged();
    } catch (caught) {
      setError(operationalError(caught, "Unable to change country availability."));
    }
  }

  if (loading) return <div className={styles.detailPanel}><LoadingState>Resolving template inheritance…</LoadingState></div>;

  return (
    <article className={styles.detailPanel}>
      <div className={styles.rowBetween}>
        <div><p className="eyebrow">{country.code}</p><h3>{country.name}</h3></div>
        <StatusBadge value={country.enabled} label={country.enabled ? "Registry enabled" : "Registry disabled"} />
      </div>
      <FormMessage message={message} />
      <FormMessage error message={error} />
      {country.effective_policy ? (
        <dl className={styles.policySummary}>
          <div><dt>Effective policy</dt><dd>Jurisdiction v{country.effective_policy.version}</dd></div>
          <div><dt>Template revision</dt><dd>v{country.effective_policy.template_version} · {currentOption?.template.key ?? "Resolving…"}</dd></div>
          <div><dt>Minimum age</dt><dd>{country.effective_policy.minimum_age}</dd></div>
          <div><dt>Fan verification</dt><dd>{country.effective_policy.fan_age_verification_required ? "Required" : "Not required"}</dd></div>
          <div><dt>Assurance</dt><dd>{formatOperationalLabel(country.effective_policy.required_assurance_level)}</dd></div>
          <div><dt>Provider</dt><dd>{country.effective_policy.age_provider}</dd></div>
          <div><dt>Creator KYC</dt><dd>{country.effective_policy.creator_identity_required ? "Required" : "Not required"}</dd></div>
          <div><dt>Performers / releases</dt><dd>{country.effective_policy.co_performer_verification_required ? "Verification" : "No verification"} · {country.effective_policy.release_required ? "Release" : "No release"}</dd></div>
        </dl>
      ) : <FormMessage error message="No single reviewed effective policy resolves for this country. Runtime access fails closed until configuration is unambiguous." />}

      <form onSubmit={saveRevision}>
        <h3>Create append-only country revision</h3>
        <div className={styles.formGrid}>
          <label>Inherited template revision
            <select onChange={(event) => { setSelectedTemplateRevisionId(event.target.value); setOverrides({}); }} required value={selectedTemplateRevisionId}>
              <option value="">Select template revision</option>
              {options.map((option) => (
                <option key={option.revision.id} value={option.revision.id}>
                  {option.template.key} · v{option.revision.version} · {option.revision.status}{option.revision.reviewed_at ? " · reviewed" : " · unreviewed"}
                </option>
              ))}
            </select>
          </label>
          <label>Status<select defaultValue="draft" name="status">{policyStatuses.map((status) => <option key={status} value={status}>{formatOperationalLabel(status)}</option>)}</select></label>
          <label>Effective from<input defaultValue={localDateTimeValue()} name="effective_from" required type="datetime-local" /></label>
          <label>Effective until<input name="effective_until" type="datetime-local" /></label>
        </div>
        {templatesTruncated ? <p className={styles.hint}>Template selection is bounded to the first 100 templates. Use Policy Templates search if the required template is not shown.</p> : null}
        {selectedOption && !selectedOption.revision.reviewed_at ? <FormMessage error message="The selected template revision is not reviewed. Publishing a country revision against it will not produce a valid runtime policy." /> : null}
        <PolicyRuleEditor overrides={overrides} onOverridesChange={setOverrides} rules={effectiveRules} />
        <div className={styles.safeChange}>
          <WeakeningWarning after={effectiveRules} before={currentRules} />
          <div className={styles.formGrid}>
            <label>Durable change reason<textarea minLength={8} onChange={(event) => setReason(event.target.value)} required value={reason} /></label>
            <div>
              <label className={styles.confirmation}><input name="reviewed" type="checkbox" />Mark this revision reviewed by me</label>
              <label className={styles.confirmation}><input name="is_demo" type="checkbox" />Demo-only configuration</label>
            </div>
          </div>
          <label className={styles.confirmation}><input checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} required type="checkbox" />I reviewed the effective inherited rules, explicit overrides, dates, and weakening warning. Create this immutable revision.</label>
          <div className={styles.formActions}><button disabled={!confirmed || reason.trim().length < 8 || !selectedTemplateRevisionId} type="submit">Create country revision</button></div>
        </div>
      </form>

      <form className={styles.safeChange} onSubmit={changeAvailability}>
        <h3>{country.enabled ? "Disable country registry entry" : "Enable country registry entry"}</h3>
        <p>{country.enabled ? "Disabling prevents this jurisdiction from resolving as available." : "Enabling expands registry availability but does not create an effective policy."}</p>
        {!country.enabled ? <FormMessage error message="Enabling a previously disabled jurisdiction is an access-expanding change." /> : null}
        <label>Change reason<textarea minLength={8} onChange={(event) => setAvailabilityReason(event.target.value)} required value={availabilityReason} /></label>
        <label className={styles.confirmation}><input checked={availabilityConfirmed} onChange={(event) => setAvailabilityConfirmed(event.target.checked)} required type="checkbox" />I confirm this country availability change.</label>
        <div className={styles.formActions}>
          <button className={country.enabled ? styles.dangerButton : styles.secondaryButton} disabled={!availabilityConfirmed || availabilityReason.trim().length < 8} type="submit">{country.enabled ? "Disable registry entry" : "Enable registry entry"}</button>
        </div>
      </form>

      <h3>Revision history</h3>
      {jurisdictions.length ? (
        <ul className={styles.timeline}>
          {jurisdictions.map((revision) => (
            <li className={styles.timelineItem} key={revision.id}>
              <div className={styles.rowBetween}>
                <strong>Jurisdiction v{revision.version}</strong>
                <span><StatusBadge value={revision.status} /> {revision.is_demo ? <span className={styles.demoBadge}>Demo</span> : null}</span>
              </div>
              <p>{formatDateTime(revision.effective_from)} → {formatDateTime(revision.effective_until)} · {Object.keys(revision.overrides).length} explicit override(s)</p>
              <p>{revision.change_reason}</p>
            </li>
          ))}
        </ul>
      ) : <EmptyState>No country revisions recorded.</EmptyState>}
    </article>
  );
}

export function ComplianceTemplates() {
  const [data, setData] = useState<PageResult<PolicyTemplate> | null>(null);
  const [page, setPage] = useState(1);
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (search) params.set("search", search);
    try {
      const next = await api<PageResult<PolicyTemplate>>(`/admin/compliance/templates?${params}`);
      setData(next);
    } catch (caught) {
      setError(operationalError(caught, "Unable to load policy templates."));
    }
  }, [page, search]);

  useEffect(() => { void load(); }, [load]);

  async function createTemplate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const created = await api<PolicyTemplate>("/admin/compliance/templates", {
        method: "POST",
        body: JSON.stringify({
          key: String(form.get("key") ?? "").trim(),
          name: String(form.get("name") ?? "").trim(),
          description: String(form.get("description") ?? "").trim() || null,
          change_reason: String(form.get("change_reason") ?? "").trim(),
        }),
      });
      setMessage(`Created template ${created.key}. It has no effective rules until a reviewed revision is added.`);
      setSelectedId(created.id);
      setShowCreate(false);
      await load();
    } catch (caught) {
      setError(operationalError(caught, "Unable to create the policy template."));
    }
  }

  const selected = data?.items.find((template) => template.id === selectedId) ?? null;

  return (
    <section className={styles.panel}>
      <SectionHeader
        action={<button className={styles.secondaryButton} onClick={() => setShowCreate((current) => !current)} type="button">{showCreate ? "Close form" : "New template"}</button>}
        description="Reusable, versioned rule sets. A country inherits one exact template revision and stores only deliberate overrides."
        eyebrow="Policy architecture"
        title="Compliance policy templates"
      />
      <FormMessage message={message} />
      <FormMessage error message={error} />
      {showCreate ? (
        <form className={styles.safeChange} onSubmit={createTemplate}>
          <h3>Create template shell</h3>
          <div className={styles.formGrid}>
            <label>Stable key<input maxLength={64} name="key" placeholder="GLOBAL_DEFAULT" required /></label>
            <label>Operator-facing name<input maxLength={120} name="name" required /></label>
          </div>
          <label>Description<textarea maxLength={500} name="description" /></label>
          <label>Creation reason<textarea minLength={8} name="change_reason" required /></label>
          <div className={styles.formActions}><button type="submit">Create template</button></div>
        </form>
      ) : null}
      <form className={styles.filters} onSubmit={(event) => { event.preventDefault(); setPage(1); setSearch(searchDraft.trim()); }}>
        <label>Search templates<input onChange={(event) => setSearchDraft(event.target.value)} placeholder="Key or name" value={searchDraft} /></label>
        <button className={styles.secondaryButton} type="submit">Search</button>
        {search ? <button className={styles.ghostButton} onClick={() => { setSearch(""); setSearchDraft(""); setPage(1); }} type="button">Clear</button> : <span />}
      </form>
      {!data && !error ? <LoadingState /> : null}
      {data ? (
        <div className={styles.templateWorkspace}>
          <div>
            {data.items.length ? (
              <ul className={styles.templateList}>
                {data.items.map((template) => (
                  <li key={template.id}>
                    <button className={`${styles.templateButton} ${selectedId === template.id ? styles.templateButtonSelected : ""}`} onClick={() => setSelectedId(template.id)} type="button">
                      <span><strong>{template.key}</strong><br /><small>{template.name}</small></span>
                      <span aria-hidden="true">→</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : <EmptyState>No templates match this search.</EmptyState>}
            <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
          </div>
          {selected ? <TemplateDetail key={selected.id} template={selected} onChanged={() => void load()} /> : <EmptyState>Select a template to inspect its immutable revision history.</EmptyState>}
        </div>
      ) : null}
    </section>
  );
}

function TemplateDetail({ template, onChanged }: { template: PolicyTemplate; onChanged: () => void }) {
  const [revisions, setRevisions] = useState<TemplateRevision[]>([]);
  const [rules, setRules] = useState<PolicyRules>(safeBlockedPolicyDraft);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await api<TemplateRevision[]>(`/admin/compliance/templates/${template.id}/revisions`);
      setRevisions(next);
      setRules(next[0]?.rules ?? safeBlockedPolicyDraft);
    } catch (caught) {
      setError(operationalError(caught, "Unable to load template revisions."));
    } finally {
      setLoading(false);
    }
  }, [template.id]);

  useEffect(() => { void load(); }, [load]);

  async function createRevision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!confirmed || reason.trim().length < 8) return;
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const created = await api<{ id: string; version: number; status: string }>(`/admin/compliance/templates/${template.id}/revisions`, {
        method: "POST",
        body: JSON.stringify(confirmedCompliancePayload({
          rules,
          status: form.get("status"),
          effective_from: new Date(String(form.get("effective_from"))).toISOString(),
          effective_until: optionalIso(form.get("effective_until")),
          reviewed: form.get("reviewed") === "on",
          is_demo: form.get("is_demo") === "on",
        }, reason)),
      });
      setMessage(`Created ${template.key} revision ${created.version} as ${created.status}.`);
      setReason("");
      setConfirmed(false);
      await load();
      onChanged();
    } catch (caught) {
      setError(operationalError(caught, "Unable to create the template revision."));
    }
  }

  if (loading) return <div className={styles.detailPanel}><LoadingState /></div>;
  const previousRules = revisions[0]?.rules ?? null;

  return (
    <article className={styles.detailPanel}>
      <p className="eyebrow">{template.key}</p>
      <h3>{template.name}</h3>
      <p>{template.description ?? "No description provided."}</p>
      <FormMessage message={message} />
      <FormMessage error message={error} />
      <form onSubmit={createRevision}>
        <h3>Create immutable template revision</h3>
        {!revisions.length ? <FormMessage error message="Safe blocked draft defaults are shown only to avoid accidental enablement. They are operational placeholders, not legal guidance." /> : null}
        <div className={styles.formGrid}>
          <label>Status<select defaultValue="draft" name="status">{policyStatuses.map((status) => <option key={status} value={status}>{formatOperationalLabel(status)}</option>)}</select></label>
          <label>Effective from<input defaultValue={localDateTimeValue()} name="effective_from" required type="datetime-local" /></label>
          <label>Effective until<input name="effective_until" type="datetime-local" /></label>
        </div>
        <PolicyRuleEditor onRulesChange={setRules} rules={rules} />
        <div className={styles.safeChange}>
          <WeakeningWarning after={rules} before={previousRules} />
          <label>Durable change reason<textarea minLength={8} onChange={(event) => setReason(event.target.value)} required value={reason} /></label>
          <div className={styles.formGrid}>
            <label className={styles.confirmation}><input name="reviewed" type="checkbox" />Mark this revision reviewed by me</label>
            <label className={styles.confirmation}><input name="is_demo" type="checkbox" />Demo-only configuration</label>
          </div>
          <label className={styles.confirmation}><input checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} required type="checkbox" />I reviewed every rule, effective window, and weakening warning. Create this append-only revision.</label>
          <div className={styles.formActions}><button disabled={!confirmed || reason.trim().length < 8} type="submit">Create template revision</button></div>
        </div>
      </form>
      <h3>Revision history</h3>
      {revisions.length ? (
        <ul className={styles.timeline}>
          {revisions.map((revision) => (
            <li className={styles.timelineItem} key={revision.id}>
              <div className={styles.rowBetween}>
                <strong>Version {revision.version}</strong>
                <span><StatusBadge value={revision.status} /> {revision.is_demo ? <span className={styles.demoBadge}>Demo</span> : null}</span>
              </div>
              <p>{formatDateTime(revision.effective_from)} → {formatDateTime(revision.effective_until)} · {revision.reviewed_at ? `Reviewed ${formatDateTime(revision.reviewed_at)}` : "Unreviewed"}</p>
              <p>{revision.change_reason}</p>
              <button className={styles.smallButton} onClick={() => setRules(revision.rules)} type="button">Use as draft starting point</button>
            </li>
          ))}
        </ul>
      ) : <EmptyState>No revisions recorded.</EmptyState>}
    </article>
  );
}

export function ComplianceFeatureFlags() {
  const [data, setData] = useState<PageResult<FeatureFlagRevision> | null>(null);
  const [page, setPage] = useState(1);
  const [feature, setFeature] = useState<ComplianceFeature>("platform_access");
  const [countryScope, setCountryScope] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await api<PageResult<FeatureFlagRevision>>(`/admin/compliance/feature-flags?page=${page}&page_size=${PAGE_SIZE}`));
    } catch (caught) {
      setError(operationalError(caught, "Unable to load feature-flag history."));
    }
  }, [page]);
  useEffect(() => { void load(); }, [load]);

  const normalizedScope = countryScope.trim().toUpperCase();
  const previous = useMemo(() => data?.items.find((revision) => revision.feature === feature && (revision.country_scope ?? "") === normalizedScope), [data?.items, feature, normalizedScope]);
  const weakening = featureFlagWeakens(previous?.enabled, enabled);

  async function createRevision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!confirmed || reason.trim().length < 8) return;
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const created = await api<{ id: string; version: number }>("/admin/compliance/feature-flags", {
        method: "POST",
        body: JSON.stringify(confirmedCompliancePayload({
          feature,
          country_scope: normalizedScope || null,
          enabled,
          effective_from: new Date(String(form.get("effective_from"))).toISOString(),
          effective_until: optionalIso(form.get("effective_until")),
          is_demo: form.get("is_demo") === "on",
        }, reason)),
      });
      setMessage(`Created ${feature} ${normalizedScope || "global"} revision ${created.version}.`);
      setConfirmed(false);
      setReason("");
      setPage(1);
      await load();
    } catch (caught) {
      setError(operationalError(caught, "Unable to create the feature-flag revision."));
    }
  }

  return (
    <section className={styles.panel}>
      <SectionHeader
        description="Operational flags are effective-dated revisions. They layer onto central jurisdiction policy; they never replace age, KYC, performer, consent, or entitlement checks."
        eyebrow="Operational controls"
        title="Feature flag revisions"
      />
      <FormMessage message={message} />
      <FormMessage error message={error} />
      <form className={styles.safeChange} onSubmit={createRevision}>
        <h3>Schedule a flag revision</h3>
        <div className={styles.formGrid}>
          <label>Feature<select onChange={(event) => setFeature(event.target.value as ComplianceFeature)} value={feature}>{complianceFeatures.map((value) => <option key={value} value={value}>{formatOperationalLabel(value)}</option>)}</select></label>
          <label>Country scope (optional ISO)<input maxLength={2} onChange={(event) => setCountryScope(event.target.value)} placeholder="Global" value={countryScope} /></label>
          <label>State<select onChange={(event) => setEnabled(event.target.value === "true")} value={String(enabled)}><option value="false">Disabled</option><option value="true">Enabled</option></select></label>
          <label>Effective from<input defaultValue={localDateTimeValue()} name="effective_from" required type="datetime-local" /></label>
          <label>Effective until<input name="effective_until" type="datetime-local" /></label>
        </div>
        {weakening ? <FormMessage error message={`Enabling ${formatOperationalLabel(feature)} for ${normalizedScope || "global scope"} expands access. Confirm central policy still enforces all required controls.`} /> : null}
        {previous ? <p className={styles.hint}>Most recent matching revision on this page: v{previous.version}, {previous.enabled ? "enabled" : "disabled"}, effective {formatDateTime(previous.effective_from)}.</p> : <p className={styles.hint}>No matching prior revision is visible on this bounded page. Review Audit if historical context is required.</p>}
        <label>Durable change reason<textarea minLength={8} onChange={(event) => setReason(event.target.value)} required value={reason} /></label>
        <label className={styles.confirmation}><input name="is_demo" type="checkbox" />Demo-only configuration</label>
        <label className={styles.confirmation}><input checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} required type="checkbox" />I confirm the feature, scope, state, effective window, and access impact.</label>
        <div className={styles.formActions}><button disabled={!confirmed || reason.trim().length < 8} type="submit">Create flag revision</button></div>
      </form>
      <h3>Append-only history</h3>
      {!data && !error ? <LoadingState /> : null}
      {data ? (
        <>
          {data.items.length ? (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead><tr><th>Feature</th><th>Scope</th><th>Version</th><th>State</th><th>Effective window</th><th>Reason</th></tr></thead>
                <tbody>
                  {data.items.map((revision) => (
                    <tr key={revision.id}>
                      <td className={styles.primaryCell}>{formatOperationalLabel(revision.feature)}</td>
                      <td><span className={styles.scopeBadge}>{revision.country_scope ?? "Global"}</span></td>
                      <td>v{revision.version}{revision.is_demo ? <> <span className={styles.demoBadge}>Demo</span></> : null}</td>
                      <td><StatusBadge value={revision.enabled} label={revision.enabled ? "Enabled" : "Disabled"} /></td>
                      <td>{formatDateTime(revision.effective_from)}<br /><small>until {formatDateTime(revision.effective_until)}</small></td>
                      <td>{revision.change_reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <EmptyState>No feature-flag revisions recorded.</EmptyState>}
          <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
        </>
      ) : null}
    </section>
  );
}
