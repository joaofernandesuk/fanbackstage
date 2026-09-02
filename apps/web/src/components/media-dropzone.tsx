"use client";

import { ChangeEvent, DragEvent, useEffect, useId, useRef, useState } from "react";

import styles from "./media-dropzone.module.css";

type CropPreset = "original" | "square" | "portrait" | "landscape";

const cropPresets: Array<{ id: CropPreset; label: string; ratio?: number }> = [
  { id: "original", label: "Original" },
  { id: "square", label: "Square 1:1", ratio: 1 },
  { id: "portrait", label: "Portrait 4:5", ratio: 4 / 5 },
  { id: "landscape", label: "Landscape 16:9", ratio: 16 / 9 },
];

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("The image could not be prepared for cropping."));
    image.src = url;
  });
}

export function MediaDropzone({
  accept,
  disabled = false,
  onChange,
  resetToken = 0,
}: {
  accept: string;
  disabled?: boolean;
  onChange: (file: File | null) => void;
  resetToken?: number;
}) {
  const inputId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [cropPreset, setCropPreset] = useState<CropPreset>("original");
  const [zoom, setZoom] = useState(1);
  const [cropError, setCropError] = useState("");
  const fileRef = useRef<File | null>(null);
  const previousResetToken = useRef(resetToken);

  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);
  useEffect(() => {
    if (previousResetToken.current === resetToken) return;
    previousResetToken.current = resetToken;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    fileRef.current = null; setFile(null); setPreviewUrl(null); setCropPreset("original"); setZoom(1); setCropError("");
  }, [previewUrl, resetToken]);

  function select(next: File | null) {
    if (!next) return;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    const image = next.type.startsWith("image/");
    const url = image ? URL.createObjectURL(next) : null;
    fileRef.current = next;
    setFile(next); setPreviewUrl(url); setCropPreset("original"); setZoom(1); setCropError("");
    onChange(next);
  }

  function onInputChange(event: ChangeEvent<HTMLInputElement>) { select(event.target.files?.[0] ?? null); }
  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault(); setDragging(false);
    if (!disabled) select(event.dataTransfer.files?.[0] ?? null);
  }

  async function applyCrop() {
    const source = fileRef.current;
    const preset = cropPresets.find((item) => item.id === cropPreset);
    if (!source || !previewUrl || !preset?.ratio) return;
    try {
      const image = await loadImage(previewUrl);
      const ratio = preset.ratio;
      const sourceRatio = image.width / image.height;
      let cropWidth = image.width; let cropHeight = image.height;
      if (sourceRatio > ratio) cropWidth = image.height * ratio;
      else cropHeight = image.width / ratio;
      cropWidth /= zoom; cropHeight /= zoom;
      const canvas = document.createElement("canvas");
      const longest = Math.max(cropWidth, cropHeight);
      const scale = longest > 2560 ? 2560 / longest : 1;
      canvas.width = Math.round(cropWidth * scale); canvas.height = Math.round(cropHeight * scale);
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Your browser cannot crop this image.");
      context.drawImage(image, (image.width - cropWidth) / 2, (image.height - cropHeight) / 2, cropWidth, cropHeight, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error("The crop could not be prepared.")), source.type === "image/png" ? "image/png" : "image/jpeg", .92));
      const extension = blob.type === "image/png" ? "png" : "jpg";
      const cropped = new File([blob], `${source.name.replace(/\.[^.]+$/, "")}-${cropPreset}.${extension}`, { type: blob.type });
      select(cropped);
    } catch (error) { setCropError(error instanceof Error ? error.message : "The crop could not be prepared."); }
  }

  return <div className={styles.root}>
    <label
      className={`${styles.dropzone} ${dragging ? styles.dragging : ""} ${disabled ? styles.disabled : ""}`}
      htmlFor={inputId}
      onDragEnter={(event) => { event.preventDefault(); if (!disabled) setDragging(true); }}
      onDragLeave={(event) => { event.preventDefault(); setDragging(false); }}
      onDragOver={(event) => event.preventDefault()}
      onDrop={onDrop}
    >
      <input accept={accept} disabled={disabled} id={inputId} onChange={onInputChange} type="file" />
      <span className={styles.icon} aria-hidden="true">⇧</span>
      <span><strong>{file ? file.name : "Drag and drop media here"}</strong><small>{file ? `${Math.ceil(file.size / 1024)} KB · choose another file to replace it` : "or browse files from your device"}</small></span>
      <span className={styles.browse}>Choose file</span>
    </label>
    {previewUrl && <div className={styles.cropPanel}>
      <img alt="Selected upload preview" className={`${styles.preview} ${styles[cropPreset]}`} src={previewUrl} />
      <div className={styles.cropControls}>
        <strong>Crop for where it will appear</strong>
        <p>Use the original for general uploads, or prepare a focused crop for a square cover, portrait card, or widescreen surface.</p>
        <div className={styles.presets}>{cropPresets.map((preset) => <button className={cropPreset === preset.id ? styles.activePreset : undefined} key={preset.id} onClick={() => setCropPreset(preset.id)} type="button">{preset.label}</button>)}</div>
        {cropPreset !== "original" && <><label>Zoom<input max="2.5" min="1" onChange={(event) => setZoom(Number(event.target.value))} step="0.05" type="range" value={zoom} /></label><button className={styles.applyCrop} onClick={() => void applyCrop()} type="button">Use this crop</button></>}
        {cropError && <p className={styles.error} role="alert">{cropError}</p>}
      </div>
    </div>}
  </div>;
}
