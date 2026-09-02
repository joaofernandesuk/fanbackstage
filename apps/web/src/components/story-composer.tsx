"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../lib/api";
import { drawFaceEffect, FACE_EFFECTS, type FaceEffectId, type NormalizedLandmark, resolveFaceEffect } from "./story-camera-effects";
import styles from "./story-composer.module.css";

type Upload = { id: string; status: string; upload_url?: string };
type AccessPolicy = "free" | "followers" | "subscription";
type Filter = "clean" | "vivid" | "warm" | "mono" | "soft";
type FaceLandmarkerInstance = { close(): void; detectForVideo(video: HTMLVideoElement, timestamp: number): { faceLandmarks: NormalizedLandmark[][] } };

const FILTERS: Array<{ id: Filter; label: string; css: string; canvas: string }> = [
  { id: "clean", label: "Clean", css: "none", canvas: "none" },
  { id: "vivid", label: "Vivid", css: "saturate(1.35) contrast(1.08)", canvas: "saturate(1.35) contrast(1.08)" },
  { id: "warm", label: "Warm", css: "sepia(.16) saturate(1.14) contrast(1.04)", canvas: "sepia(.16) saturate(1.14) contrast(1.04)" },
  { id: "mono", label: "Mono", css: "grayscale(1) contrast(1.13)", canvas: "grayscale(1) contrast(1.13)" },
  { id: "soft", label: "Soft", css: "brightness(1.06) saturate(.86)", canvas: "brightness(1.06) saturate(.86)" },
];
const CANVAS_WIDTH = 1080;
const CANVAS_HEIGHT = 1920;
const CAMERA_WIDTH = 720;
const CAMERA_HEIGHT = 1280;
const wait = (milliseconds: number) => new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

async function loadImage(source: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => { const image = new Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error("The selected image could not be opened.")); image.src = source; });
}

function drawCameraFrame(context: CanvasRenderingContext2D, video: HTMLVideoElement, effect: FaceEffectId, landmarks: readonly NormalizedLandmark[] | undefined) {
  const scale = Math.max(CAMERA_WIDTH / video.videoWidth, CAMERA_HEIGHT / video.videoHeight);
  const width = video.videoWidth * scale; const height = video.videoHeight * scale;
  const offsetX = (CAMERA_WIDTH - width) / 2; const offsetY = (CAMERA_HEIGHT - height) / 2;
  context.fillStyle = "#080b1b"; context.fillRect(0, 0, CAMERA_WIDTH, CAMERA_HEIGHT);
  context.drawImage(video, offsetX, offsetY, width, height);
  drawFaceEffect(context, effect, landmarks, video.videoWidth, video.videoHeight, offsetX, offsetY, scale);
}

export function StoryComposer() {
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [sourceName, setSourceName] = useState("story.jpg");
  const [filter, setFilter] = useState<Filter>("clean");
  const [zoom, setZoom] = useState(1); const [offsetX, setOffsetX] = useState(0); const [offsetY, setOffsetY] = useState(0);
  const [text, setText] = useState(""); const [emoji, setEmoji] = useState("✨"); const [textColor, setTextColor] = useState("#ffffff");
  const [accessPolicy, setAccessPolicy] = useState<AccessPolicy>("free"); const [caption, setCaption] = useState(""); const [altText, setAltText] = useState("");
  const [working, setWorking] = useState(false); const [notice, setNotice] = useState(""); const [error, setError] = useState("");
  const [cameraActive, setCameraActive] = useState(false); const [cameraLoading, setCameraLoading] = useState(false); const [cameraError, setCameraError] = useState("");
  const [faceEffect, setFaceEffect] = useState<FaceEffectId>("none");
  const [trackingStatus, setTrackingStatus] = useState<"off" | "loading" | "ready" | "tracking" | "unavailable">("off");
  const [fileDragging, setFileDragging] = useState(false);
  const sourceRef = useRef<string | null>(null); const cameraVideoRef = useRef<HTMLVideoElement | null>(null); const cameraCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const cameraStreamRef = useRef<MediaStream | null>(null); const landmarkerRef = useRef<FaceLandmarkerInstance | null>(null); const frameRef = useRef<number | null>(null);
  const landmarksRef = useRef<NormalizedLandmark[] | undefined>(undefined); const lastDetectionRef = useRef(0); const mountedRef = useRef(true);

  function releaseCamera() {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null; cameraStreamRef.current?.getTracks().forEach((track) => track.stop()); cameraStreamRef.current = null;
    if (cameraVideoRef.current) cameraVideoRef.current.srcObject = null;
    landmarksRef.current = undefined; setCameraActive(false); setTrackingStatus("off");
  }
  useEffect(() => () => { mountedRef.current = false; if (sourceRef.current) URL.revokeObjectURL(sourceRef.current); if (frameRef.current !== null) cancelAnimationFrame(frameRef.current); cameraStreamRef.current?.getTracks().forEach((track) => track.stop()); landmarkerRef.current?.close(); }, []);

  function selectSource(file: File | null) {
    if (!file) return;
    if (!file.type.startsWith("image/")) { setError("Story composer v1 supports photos. Video Stories can still be published from the existing media library while video editing is built."); return; }
    if (sourceRef.current) URL.revokeObjectURL(sourceRef.current);
    const next = URL.createObjectURL(file); sourceRef.current = next; setSourceUrl(next); setSourceName(file.name); setError(""); setNotice(""); setZoom(1); setOffsetX(0); setOffsetY(0);
  }
  async function enableLandmarker() {
    if (landmarkerRef.current) return landmarkerRef.current;
    setTrackingStatus("loading");
    try {
      const { FaceLandmarker, FilesetResolver } = await import("@mediapipe/tasks-vision");
      const fileset = await FilesetResolver.forVisionTasks("/mediapipe/wasm");
      const landmarker = await FaceLandmarker.createFromOptions(fileset, { baseOptions: { modelAssetPath: "/mediapipe/models/face_landmarker.task" }, runningMode: "VIDEO", numFaces: 1, minFaceDetectionConfidence: .5, minFacePresenceConfidence: .5, minTrackingConfidence: .5 });
      landmarkerRef.current = landmarker; if (mountedRef.current) setTrackingStatus("ready"); return landmarker;
    } catch { if (mountedRef.current) { setTrackingStatus("unavailable"); setCameraError("Face effects are unavailable in this browser. You can still capture a camera photo without an effect."); } return null; }
  }
  async function startCamera() {
    setCameraError(""); setError("");
    if (!navigator.mediaDevices?.getUserMedia) { setCameraError("This browser does not support camera capture. Choose a photo instead."); return; }
    setCameraLoading(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: "user" }, width: { ideal: CAMERA_WIDTH }, height: { ideal: CAMERA_HEIGHT } } });
      if (!mountedRef.current) { stream.getTracks().forEach((track) => track.stop()); return; }
      cameraStreamRef.current = stream; setCameraActive(true);
    } catch { setCameraError("Camera access was not granted. Nothing was captured or uploaded; choose a photo instead."); }
    finally { if (mountedRef.current) setCameraLoading(false); }
  }
  useEffect(() => {
    if (!cameraActive) return;
    const video = cameraVideoRef.current; const canvas = cameraCanvasRef.current;
    if (!video || !canvas || !cameraStreamRef.current) return;
    let cancelled = false; canvas.width = CAMERA_WIDTH; canvas.height = CAMERA_HEIGHT; video.srcObject = cameraStreamRef.current;
    const preview = async () => {
      try {
        await video.play(); if (faceEffect !== "none") await enableLandmarker(); const context = canvas.getContext("2d"); if (!context || cancelled) return;
        const renderFrame = () => {
          if (cancelled || !video.videoWidth) return; const now = performance.now();
          if (faceEffect !== "none" && landmarkerRef.current && now - lastDetectionRef.current > 90) {
            try { landmarksRef.current = landmarkerRef.current.detectForVideo(video, now).faceLandmarks[0]; lastDetectionRef.current = now; if (landmarksRef.current?.length) setTrackingStatus("tracking"); } catch { /* keep the last safe local result */ }
          }
          drawCameraFrame(context, video, faceEffect, landmarksRef.current); frameRef.current = requestAnimationFrame(renderFrame);
        }; renderFrame();
      } catch { if (!cancelled) setCameraError("The camera preview could not start. Choose a photo instead."); }
    };
    void preview(); return () => { cancelled = true; if (frameRef.current !== null) cancelAnimationFrame(frameRef.current); };
  }, [cameraActive, faceEffect]);
  function captureCameraPhoto() {
    const canvas = cameraCanvasRef.current; if (!canvas) return;
    canvas.toBlob((blob) => { if (!blob) { setCameraError("The camera photo could not be captured. Please try again."); return; } selectSource(new File([blob], `story-camera-${Date.now()}.jpg`, { type: "image/jpeg" })); releaseCamera(); setNotice("Camera photo captured locally. Add your Story text, then publish when you are ready."); }, "image/jpeg", .92);
  }
  async function render(): Promise<Blob> {
    if (!sourceUrl) throw new Error("Choose a photo first."); const image = await loadImage(sourceUrl); const canvas = document.createElement("canvas"); canvas.width = CANVAS_WIDTH; canvas.height = CANVAS_HEIGHT;
    const context = canvas.getContext("2d"); if (!context) throw new Error("Your browser cannot prepare this Story image."); const selectedFilter = FILTERS.find((item) => item.id === filter) ?? FILTERS[0];
    const baseScale = Math.max(CANVAS_WIDTH / image.width, CANVAS_HEIGHT / image.height) * zoom; const width = image.width * baseScale; const height = image.height * baseScale;
    const overflowX = Math.max(0, width - CANVAS_WIDTH); const overflowY = Math.max(0, height - CANVAS_HEIGHT); const x = (CANVAS_WIDTH - width) / 2 - (offsetX / 100) * overflowX / 2; const y = (CANVAS_HEIGHT - height) / 2 - (offsetY / 100) * overflowY / 2;
    context.fillStyle = "#090b18"; context.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT); context.filter = selectedFilter.canvas; context.drawImage(image, x, y, width, height); context.filter = "none"; context.fillStyle = "rgba(2, 5, 18, .34)"; context.fillRect(0, CANVAS_HEIGHT * .73, CANVAS_WIDTH, CANVAS_HEIGHT * .27); context.textAlign = "center"; context.fillStyle = textColor; context.font = "700 54px Arial, sans-serif";
    const message = text.trim(); if (message) { const lines: string[] = []; let line = ""; for (const word of message.split(/\s+/)) { const candidate = line ? `${line} ${word}` : word; if (context.measureText(candidate).width > 820 && line) { lines.push(line); line = word; } else line = candidate; } if (line) lines.push(line); lines.slice(0, 4).forEach((value, index) => context.fillText(value, CANVAS_WIDTH / 2, CANVAS_HEIGHT - 260 + index * 68)); }
    if (emoji) { context.font = "88px Arial, sans-serif"; context.fillText(emoji, CANVAS_WIDTH / 2, CANVAS_HEIGHT - 90); }
    return new Promise<Blob>((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("The composed Story image could not be created.")), "image/jpeg", .92));
  }
  async function waitForReady(assetId: string) { for (let attempt = 0; attempt < 30; attempt += 1) { await wait(1000); const assets = await api<Upload[]>("/media/mine"); const asset = assets.find((candidate) => candidate.id === assetId); if (asset?.status === "ready") return; if (asset?.status === "failed") throw new Error("The Story image could not be processed. Please try another photo."); } throw new Error("Story image processing is still running. It was uploaded safely; refresh Creator Studio shortly and publish it from the media library."); }
  async function publish() {
    setError(""); setNotice(""); setWorking(true);
    try { const blob = await render(); const filename = `story-${crypto.randomUUID()}-${sourceName.replace(/\.[^.]+$/, "")}.jpg`; const created = await api<Upload>("/media/uploads", { method: "POST", body: JSON.stringify({ filename, mime_type: "image/jpeg" }) }); if (!created.upload_url) throw new Error("The secure upload authorisation is missing."); const upload = await fetch(created.upload_url, { method: "PUT", headers: { "Content-Type": "image/jpeg" }, body: blob }); if (!upload.ok) throw new Error(`Secure image upload failed (${upload.status}).`); await api<Upload>(`/media/${created.id}/finalize`, { method: "POST" }); setNotice("Story image uploaded. Waiting for its secure display derivative…"); await waitForReady(created.id); await api("/stories", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ media_asset_id: created.id, access_policy: accessPolicy, caption: caption.trim() || undefined, alt_text: altText.trim() || undefined }) }); setNotice("Story published. It is available to authorised viewers for 24 hours."); }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : caught instanceof Error ? caught.message : "The Story could not be published."); } finally { setWorking(false); }
  }
  const selectedFilter = FILTERS.find((item) => item.id === filter) ?? FILTERS[0]; const transform = `scale(${zoom}) translate(${offsetX / zoom}%, ${offsetY / zoom}%)`;
  return <section aria-labelledby="story-composer-heading" className={styles.shell}>
    <header className={styles.header}><div><p className="eyebrow">CREATOR STORY STUDIO</p><h1 id="story-composer-heading">Create a Story</h1><p>Compose a vertical photo, then publish it through the same protected media and 24-hour Story lifecycle used everywhere else.</p></div><Link className={styles.backLink} href="/stories">View active Stories</Link></header>
    <div className={styles.layout}><div className={styles.previewFrame}><div aria-label="Story preview" className={styles.preview}>{cameraActive ? <><video aria-hidden="true" className={styles.cameraSource} muted playsInline ref={cameraVideoRef} /><canvas aria-label="Live camera preview with the selected face effect" className={styles.cameraCanvas} ref={cameraCanvasRef} /></> : sourceUrl ? <img alt="Story composition preview" src={sourceUrl} style={{ filter: selectedFilter.css, transform }} /> : <div className={styles.emptyPreview}><strong>Choose a photo or open the camera</strong><span>Your finished Story is framed for a phone screen.</span></div>}{!cameraActive && sourceUrl && <div className={styles.previewShade} />}{!cameraActive && sourceUrl && (text.trim() || emoji) && <div className={styles.previewCopy} style={{ color: textColor }}>{text.trim() && <strong>{text}</strong>}{emoji && <span>{emoji}</span>}</div>}</div><p>{cameraActive ? "Live preview only. The camera feed and face landmarks stay on this device." : "Photo editor publishes a new Story-only image. Your original file stays on your device and is never used for public Story delivery."}</p></div>
      <div className={styles.controls}>
        <fieldset className={styles.cameraPanel} disabled={working}><legend>Camera photo and face effects</legend><p>Open the camera only when you are ready. Face landmarks are processed locally to position the selected effect; no camera frames or face data are uploaded until you choose Publish.</p><label>Face effect<select onChange={(event) => { const next = resolveFaceEffect(event.target.value); setFaceEffect(next); if (next !== "none" && cameraActive) void enableLandmarker(); }} value={faceEffect}>{FACE_EFFECTS.map((effect) => <option key={effect.id} value={effect.id}>{effect.label} — {effect.description}</option>)}</select></label><div className={styles.cameraActions}>{cameraActive ? <><button className={styles.capture} disabled={cameraLoading} onClick={captureCameraPhoto} type="button">Capture photo</button><button className={styles.secondary} onClick={releaseCamera} type="button">Close camera</button></> : <button className={styles.capture} disabled={cameraLoading} onClick={() => void startCamera()} type="button">{cameraLoading ? "Opening camera…" : "Open camera"}</button>}</div>{faceEffect !== "none" && <p className={styles.trackingStatus}>{trackingStatus === "tracking" ? "Face effect is tracking locally." : trackingStatus === "loading" ? "Loading the local face-effect model…" : trackingStatus === "unavailable" ? "Face effects unavailable; camera capture remains available." : "Point your face towards the camera to apply the effect."}</p>}{cameraError && <p className={styles.error} role="alert">{cameraError}</p>}</fieldset>
        <label className={`${styles.filePicker} ${fileDragging ? styles.fileDragging : ""}`} onDragEnter={(event) => { event.preventDefault(); if (!working && !cameraActive) setFileDragging(true); }} onDragLeave={(event) => { event.preventDefault(); setFileDragging(false); }} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); setFileDragging(false); if (!working && !cameraActive) selectSource(event.dataTransfer.files?.[0] ?? null); }}><span><strong>Drop a photo here</strong><small>or choose a photo from your device</small></span><input accept="image/jpeg,image/png,image/webp" disabled={working || cameraActive} onChange={(event) => selectSource(event.target.files?.[0] ?? null)} type="file" /><em>Choose photo</em></label>
        <fieldset disabled={!sourceUrl || working || cameraActive}><legend>Crop and framing</legend><label>Zoom<input max="2.5" min="1" onChange={(event) => setZoom(Number(event.target.value))} step="0.05" type="range" value={zoom} /></label><label>Move left / right<input max="100" min="-100" onChange={(event) => setOffsetX(Number(event.target.value))} type="range" value={offsetX} /></label><label>Move up / down<input max="100" min="-100" onChange={(event) => setOffsetY(Number(event.target.value))} type="range" value={offsetY} /></label></fieldset>
        <fieldset disabled={!sourceUrl || working || cameraActive}><legend>Look</legend><div className={styles.filterChoices}>{FILTERS.map((item) => <button className={filter === item.id ? styles.selectedFilter : undefined} key={item.id} onClick={() => setFilter(item.id)} type="button">{item.label}</button>)}</div></fieldset>
        <fieldset disabled={!sourceUrl || working || cameraActive}><legend>Text and emoji</legend><label>Story text<textarea maxLength={160} onChange={(event) => setText(event.target.value)} placeholder="Say something…" value={text} /></label><div className={styles.inlineFields}><label>Emoji<select onChange={(event) => setEmoji(event.target.value)} value={emoji}><option value="✨">✨ Sparkle</option><option value="❤️">❤️ Love</option><option value="🔥">🔥 Fire</option><option value="😍">😍 Love it</option><option value="🎉">🎉 Celebrate</option><option value="">No emoji</option></select></label><label>Text colour<input aria-label="Text colour" onChange={(event) => setTextColor(event.target.value)} type="color" value={textColor} /></label></div></fieldset>
        <fieldset disabled={!sourceUrl || working || cameraActive}><legend>Publish</legend><label>Who can view<select onChange={(event) => setAccessPolicy(event.target.value as AccessPolicy)} value={accessPolicy}><option value="free">Everyone with Story access</option><option value="followers">Followers</option><option value="subscription">Subscribers</option></select></label><label>Caption (optional)<textarea maxLength={2000} onChange={(event) => setCaption(event.target.value)} value={caption} /></label><label>Alt text (optional)<textarea maxLength={500} onChange={(event) => setAltText(event.target.value)} value={altText} /></label><button className={styles.publish} disabled={!sourceUrl || working} onClick={() => void publish()} type="button">{working ? "Preparing secure Story…" : "Publish Story"}</button></fieldset>
        {notice && <p className={styles.notice} role="status">{notice}</p>}{error && <p className={styles.error} role="alert">{error}</p>}
      </div></div>
  </section>;
}
