export type FaceEffectId = "none" | "lip-colour" | "neon-eyes" | "star-mask";

export type NormalizedLandmark = { x: number; y: number; z?: number };

export const FACE_EFFECTS: ReadonlyArray<{ id: FaceEffectId; label: string; description: string }> = [
  { id: "none", label: "No effect", description: "Capture the camera image as it is." },
  { id: "lip-colour", label: "Lip colour", description: "A soft berry tint follows your lips." },
  { id: "neon-eyes", label: "Neon eyes", description: "A subtle cyan glow follows your eyes." },
  { id: "star-mask", label: "Star mask", description: "A lightweight star mask follows your face." },
];

const effectIds = new Set(FACE_EFFECTS.map((effect) => effect.id));

export function resolveFaceEffect(value: string): FaceEffectId {
  return effectIds.has(value as FaceEffectId) ? value as FaceEffectId : "none";
}

function point(landmarks: readonly NormalizedLandmark[], index: number, width: number, height: number, offsetX: number, offsetY: number, scale: number) {
  const landmark = landmarks[index];
  if (!landmark) return null;
  return { x: offsetX + landmark.x * width * scale, y: offsetY + landmark.y * height * scale };
}

function oval(context: CanvasRenderingContext2D, center: { x: number; y: number }, radiusX: number, radiusY: number) {
  context.beginPath();
  context.ellipse(center.x, center.y, radiusX, radiusY, 0, 0, Math.PI * 2);
}

export function drawFaceEffect(
  context: CanvasRenderingContext2D,
  effect: FaceEffectId,
  landmarks: readonly NormalizedLandmark[] | undefined,
  sourceWidth: number,
  sourceHeight: number,
  offsetX: number,
  offsetY: number,
  scale: number,
) {
  if (!landmarks?.length || effect === "none") return;
  const leftEye = point(landmarks, 33, sourceWidth, sourceHeight, offsetX, offsetY, scale);
  const rightEye = point(landmarks, 263, sourceWidth, sourceHeight, offsetX, offsetY, scale);
  const upperLip = point(landmarks, 0, sourceWidth, sourceHeight, offsetX, offsetY, scale);
  const lowerLip = point(landmarks, 17, sourceWidth, sourceHeight, offsetX, offsetY, scale);
  const leftFace = point(landmarks, 234, sourceWidth, sourceHeight, offsetX, offsetY, scale);
  const rightFace = point(landmarks, 454, sourceWidth, sourceHeight, offsetX, offsetY, scale);

  context.save();
  if (effect === "lip-colour" && upperLip && lowerLip) {
    const center = { x: (upperLip.x + lowerLip.x) / 2, y: (upperLip.y + lowerLip.y) / 2 };
    const radiusY = Math.max(7, Math.abs(lowerLip.y - upperLip.y) * 1.8);
    context.fillStyle = "rgba(218, 43, 111, .52)";
    context.shadowBlur = 10;
    context.shadowColor = "rgba(236, 72, 153, .7)";
    oval(context, center, radiusY * 3.3, radiusY);
    context.fill();
  }
  if (effect === "neon-eyes" && leftEye && rightEye) {
    context.strokeStyle = "rgba(56, 210, 255, .92)";
    context.lineWidth = 3;
    context.shadowBlur = 15;
    context.shadowColor = "rgba(14, 165, 255, .9)";
    for (const eye of [leftEye, rightEye]) {
      oval(context, eye, 22, 11);
      context.stroke();
    }
  }
  if (effect === "star-mask" && leftFace && rightFace && leftEye && rightEye) {
    const width = Math.max(76, Math.abs(rightFace.x - leftFace.x) * .78);
    const height = width * .35;
    const center = { x: (leftEye.x + rightEye.x) / 2, y: (leftEye.y + rightEye.y) / 2 };
    context.fillStyle = "rgba(120, 62, 246, .35)";
    context.strokeStyle = "rgba(236, 72, 153, .8)";
    context.lineWidth = 3;
    context.shadowBlur = 16;
    context.shadowColor = "rgba(139, 92, 246, .8)";
    context.beginPath();
    context.roundRect(center.x - width / 2, center.y - height / 2, width, height, height / 2);
    context.fill();
    context.stroke();
    context.fillStyle = "#ffe36e";
    context.font = `${Math.max(18, width * .24)}px sans-serif`;
    context.textAlign = "center";
    context.fillText("✦", center.x, center.y + height * .12);
  }
  context.restore();
}
