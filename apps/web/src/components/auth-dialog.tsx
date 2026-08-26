"use client";

import Image from "next/image";
import { MouseEvent, useEffect, useRef } from "react";

import { AuthMode } from "../lib/auth-ui";
import { AuthForm } from "./auth-form";
import styles from "./auth-dialog.module.css";

export function AuthDialog({
  mode,
  nextPath,
  onClose,
  onModeChange,
}: {
  mode: AuthMode;
  nextPath: string;
  onClose: () => void;
  onModeChange: (mode: AuthMode) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    requestAnimationFrame(() => dialog.querySelector<HTMLInputElement>('input[type="email"]')?.focus());
  }, []);

  function dismiss() {
    dialogRef.current?.close();
  }

  function backdrop(event: MouseEvent<HTMLDialogElement>) {
    if (event.target === event.currentTarget) dismiss();
  }

  function success(destination: string) {
    dismiss();
    requestAnimationFrame(() => window.location.assign(destination));
  }

  return (
    <dialog
      aria-label={mode === "login" ? "Log in to FanBackstage" : "Join FanBackstage"}
      className={styles.dialog}
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
      onClick={backdrop}
      onClose={onClose}
      ref={dialogRef}
    >
      <div className={styles.panel}>
        <div className={styles.dialogHeader}>
          <span aria-hidden="true" className={styles.mark}>
            <Image alt="" height={31} src="/brand/fanbackstage_symbol_transparent.png" width={46} />
          </span>
          <div aria-label="Choose authentication mode" className={styles.modeSwitch} role="group">
            <button aria-pressed={mode === "login"} onClick={() => onModeChange("login")} type="button">Log in</button>
            <button aria-pressed={mode === "register"} onClick={() => onModeChange("register")} type="button">Join</button>
          </div>
          <button aria-label="Close authentication dialog" className={styles.close} onClick={dismiss} type="button">×</button>
        </div>
        <AuthForm
          key={mode}
          mode={mode}
          nextPath={nextPath}
          onModeChange={onModeChange}
          onSuccess={success}
          presentation="dialog"
        />
      </div>
    </dialog>
  );
}
