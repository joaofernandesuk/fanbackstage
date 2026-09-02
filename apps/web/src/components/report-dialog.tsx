"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { api, ApiError } from "../lib/api";
import styles from "./report-dialog.module.css";

type ReportReason = { value: string; label: string };

export type ReportTarget = {
  targetType: "post" | "comment" | "message" | "story" | "media" | "live_room" | "marketplace_listing";
  targetId: string;
  label: string;
};

export function ReportDialog({
  target,
  onClose,
  onSubmitted,
  submitPath,
}: {
  target: ReportTarget | null;
  onClose: () => void;
  onSubmitted?: () => void;
  /** Legacy domain report boundaries can share the same canonical report UI. */
  submitPath?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [reasons, setReasons] = useState<ReportReason[]>([]);
  const [reason, setReason] = useState("");
  const [details, setDetails] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!target) return;
    dialog.current?.showModal();
    setReason("");
    setDetails("");
    setError("");
    void api<{ reasons: ReportReason[] }>("/trust-safety/report-options")
      .then((result) => setReasons(result.reasons))
      .catch((caught) =>
        setError(caught instanceof ApiError ? caught.message : "Unable to load report options"),
      );
  }, [target]);

  function close() {
    dialog.current?.close();
    onClose();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!target || !reason || (reason === "other" && !details.trim())) return;
    setWorking(true);
    setError("");
    try {
      await api(submitPath ?? "/trust-safety/reports", {
        method: "POST",
        body: JSON.stringify({
          ...(submitPath ? {} : {
            target_type: target.targetType,
            target_id: target.targetId,
          }),
          reason,
          details: details.trim() || undefined,
        }),
      });
      onSubmitted?.();
      close();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to send report");
    } finally {
      setWorking(false);
    }
  }

  return (
    <dialog
      aria-labelledby="report-dialog-title"
      className={styles.dialog}
      onCancel={close}
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
      ref={dialog}
    >
      <form className={styles.panel} onSubmit={(event) => void submit(event)}>
        <div className={styles.header}>
          <div>
            <span>SAFETY &amp; REPORTING</span>
            <h2 id="report-dialog-title">Report {target?.label}</h2>
          </div>
          <button aria-label="Close report form" onClick={close} type="button">×</button>
        </div>
        <p>Choose the reason that best fits. Reports go to the safety team for review.</p>
        <label>
          Reason
          <select onChange={(event) => setReason(event.target.value)} required value={reason}>
            <option value="">Select a reason</option>
            {reasons.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label>
          {reason === "other" ? "Tell us what happened" : "Additional details (optional)"}
          <textarea
            maxLength={2000}
            onChange={(event) => setDetails(event.target.value)}
            placeholder={reason === "other" ? "Please describe the issue" : "Add context that may help our review"}
            required={reason === "other"}
            value={details}
          />
        </label>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <div className={styles.actions}>
          <button onClick={close} type="button">Cancel</button>
          <button disabled={working || !reason || (reason === "other" && !details.trim())} type="submit">
            {working ? "Sending…" : "Send report"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
