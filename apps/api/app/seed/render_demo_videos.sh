#!/usr/bin/env bash
set -euo pipefail

# Rebuild the harmless repository-owned demo masters and their deliberately
# shorter acquisition trailers. Keep this list aligned with manifest.CREATORS.
asset_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/assets" && pwd)"
slugs=(
  aria-group
  atlas-reed
  ivy-ember
  luna-sparks
  mira-nova
  nora-market
  nova-blue
  sera-kim
  sienna-ray
  skye-live
  valentina-cruz
  zara-pulse
)

for slug in "${slugs[@]}"; do
  ffmpeg -hide_banner -loglevel error -y \
    -loop 1 -framerate 12 -i "${asset_root}/${slug}.jpg" \
    -t 8 -an -vf "scale=640:480:force_original_aspect_ratio=increase,crop=640:480,format=yuv420p" \
    -c:v libx264 -preset veryslow -crf 32 -movflags +faststart \
    "${asset_root}/${slug}.mp4"
  ffmpeg -hide_banner -loglevel error -y \
    -loop 1 -framerate 12 -i "${asset_root}/${slug}.jpg" \
    -t 2 -an -vf "scale=640:480:force_original_aspect_ratio=increase,crop=640:480,format=yuv420p" \
    -c:v libx264 -preset veryslow -crf 34 -movflags +faststart \
    "${asset_root}/${slug}-preview.mp4"
done
