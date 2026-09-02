# Third-party notices

## Story camera face effects

The browser-only Story camera uses `@mediapipe/tasks-vision` version `1.0.1` and its WebAssembly runtime, under the Apache License 2.0. The pinned Face Landmarker float16 model (release `1`) is served locally from `apps/web/public/mediapipe/models/face_landmarker.task`; its SHA-256 is `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`.

The runtime and model are vendored so the editor does not send camera frames, face landmarks, or effect requests to a third party. The model is used only after the creator explicitly opens the Story camera.
