# Local demo

All demo data is fictional, harmless, and development-only. `fanbackstage-dev` is the persistent Docker Desktop project for manual testing; it remains separate from disposable release-validation projects.

## Start, seed, and validate

```sh
make dev
make demo-seed
make demo-seed
make demo-validate
make dev-status
```

The second seed pass is intentional: it proves the manifest converges without adding another copy. `make demo-validate` is read-only and exits non-zero if counts, creator states, titles, or visual references no longer satisfy the local manifest. Seeding and validation both refuse unless `FANBACKSTAGE_ENVIRONMENT=development` and `FANBACKSTAGE_DEMO_SEED_ENABLED=true`; the guard runs before database or storage access.

Open the web app at `http://localhost:13000`, API at `http://localhost:18000`, API documentation at `http://localhost:18000/docs`, Mailpit at `http://localhost:18035`, and MinIO at `http://localhost:19011`. LiveKit listens on `ws://localhost:17880`.

The fixed development-only ports are PostgreSQL `15432`, Redis `16390`, SMTP `11035`, Mailpit `18035`, MinIO `19010/19011`, API `18000`, web `13000`, and LiveKit `17880/17881/17882`. The seed remains opt-in and never runs automatically during application startup.

## Credentials

Every account uses `fanbackstage-demo-local-only`. Never reuse this password or these accounts outside the disposable local environment.

All listed fictional accounts converge to the current baseline adult self-attestation on
every seed run, including accounts created by an older schema version. This convergence is
limited to the known demo manifest and never backfills arbitrary users. Demo media meant
for anonymous browsing is explicitly moderator-classified `safe_public`; the fail-closed
model default remains `adult_restricted`.

| Persona | Email | What to test |
| --- | --- | --- |
| Admin | admin@demo.fanbackstage.local | administration, moderation, and Featuring configuration |
| Moderator | moderator@demo.fanbackstage.local | ordinary moderation |
| Sensitive reviewer | evidence-moderator@demo.fanbackstage.local | restricted evidence permission via super-admin |
| Group manager | manager@demo.fanbackstage.local | two accepted agency/group contracts and delegated permissions |
| New fan | newfan@demo.fanbackstage.local | discovery, follows, comments, and messages |
| Subscriber | subscriber@demo.fanbackstage.local | active subscription and creator conversation |
| PPV buyer | ppvbuyer@demo.fanbackstage.local | attributed referral and settled PPV purchase |
| Marketplace buyer | marketbuyer@demo.fanbackstage.local | paid, shipped, and delivered demo order |
| Social fan | socialfan@demo.fanbackstage.local | feed reactions, comments, and messages |
| Marketing opted in | marketing-in@demo.fanbackstage.local | consented marketing email eligibility and referral ownership |
| Marketing opted out | marketing-out@demo.fanbackstage.local | marketing suppression by preference while transactional email remains eligible |
| Creator | luna-sparks@demo.fanbackstage.local | established profile, content, shop, and active Featuring placement |
| Creator | skye-live@demo.fanbackstage.local | safely ended public-live history |
| Restricted creator | reya-restricted@demo.fanbackstage.local | suspended, non-public Trust & Safety state |

`fan01` through `fan16` also exist at `fanNN@demo.fanbackstage.local` for denser social, subscription, PPV, and marketplace examples.

## Deterministic dataset

The validator expects exactly:

- 40 users;
- 13 creator profiles: 12 approved/public and `reya-restricted` suspended/non-public;
- two real groups/agencies with accepted, versioned financial contracts;
- 48 published feed posts (four per public creator);
- 27 published content items: one baseline gallery and one video per public creator, plus three multi-image showcase galleries; the total includes 12 videos;
- 18 published physical marketplace listings;
- 24 active authoritative Stories across eight creators, plus at least four expired historical Stories.

It also requires a dense follow/reaction/comment graph, at least three conversations with message history, settled subscriptions and PPV purchases, three marketplace orders in varied states, balanced ledger transactions, one signup referral attribution and reward allocation, transactional notifications, an active paid Featuring example, and safely ended live-room history. Financial entitlements, immutable ledger entries, group allocation snapshots, referral allocations, and Featuring settlement are produced through their owning domain services and signed development payment webhooks; the seed never inserts them directly.

The `marketing-in` and `marketing-out` personas have real notification-domain marketing preference rows. `marketing-in` has explicit email consent and no suppression; `marketing-out` has marketing email disabled with no consent while in-app notifications remain enabled. The seed reaches both states through the audited notification preference service and skips an already-converged state, so a second seed pass neither moves the consent timestamp nor adds another audit event.

All 12 public creators have enabled EUR subscription prices for `month_1`, `month_3`, `month_6`, and `month_12`. Prices vary deterministically by persona. Galleries rotate through free, follower, and subscription access; the showcase set adds ordered free, subscription, and €12.99 PPV four-image examples. Videos rotate through free, subscription, and PPV access, giving the demo genuine entitlement states rather than visual-only locks. Zara's PPV showcase and video provide harmless `adult_restricted` policy fixtures: their safe acquisition media remains separate from restricted derivatives and requires the real server acknowledgement flow.

Creator visuals use stable public references:

```text
/demo/creators/<slug>/avatar.jpg
/demo/creators/<slug>/cover.jpg
```

Repository-owned JPEG masters live under `apps/api/app/seed/assets/`. Images use the normal private upload, server-side finalize, and media-processing path. The same harmless masters are rendered into small eight-second MP4 playback fixtures and distinct two-second `*-preview.mp4` trailers checked in beside them. `apps/api/app/seed/render_demo_videos.sh` is the deterministic rebuild recipe. The slim development API image has no ffmpeg binary, so the seed’s narrowly scoped development adapter uploads those MP4s through the normal private storage boundary and installs their pre-rendered poster/playback/preview derivative metadata. The validator requires the trailer duration to be exactly two seconds and strictly shorter than both the eight-second playback derivative and source metadata. This adapter accepts only repository paths from the immutable creator manifest and is unreachable from product APIs.

The local web client uses the existing development payment completion hooks only when `NODE_ENV` is not `production`, allowing manual PPV and subscription entitlement testing against signed development callbacks. Production builds never invoke those development settlement endpoints: they leave the attempt pending until the configured external provider confirms it. There is no wallet or credits domain in this demo or in the consumer UI.

Inside Docker, API storage I/O stays on the private `minio:9000` network address. Presigned browser URLs use the separate `http://localhost:19010` signing endpoint, so Story media and creator uploads work from the local web origin without making the bucket public or leaking original object keys through application payloads.

Stories reuse the same checked-in local masters, creator-owned private media pipeline, and authorised derivative types as the content system, while creating distinct Story-safe `MediaAsset` rows so no paid/private content delivery contract can be broadened. Each active demo cohort uses the real 24-hour lifecycle; two free Stories per seeded Story creator keep the anonymous consumer rail useful, while a smaller set exercises follower/subscription access. Expired rows remain durable historical state and never return to the consumer rail. Running the seed again immediately is count-stable; after a cohort naturally expires, a later seed run creates a fresh active cohort without reviving or deleting the historical records. Highlights remain intentionally out of scope.

Live history is ended within the same uncommitted seed transaction, so the application never exposes a fake active room without a broadcaster. Private paid-live sessions are also omitted because their canonical state requires genuine provider attendance events. Analytics remain derived from the canonical interactions above; the seed does not fabricate analytics rows.

## Reset and quick checks

`make dev-reset` is guarded to a development database URL on localhost and recreates only Compose-owned volumes. `make dev-backup` creates a disposable PostgreSQL dump under `.fanbackstage-dev/backups/`. Perform restore drills only against this reset local stack; production restore procedures belong to the hosting runbook.

For a browser pass, sign in as `subscriber`, browse discovery, subscribe, and open messages; sign in as `ppvbuyer` to inspect purchased content; sign in as `marketbuyer` for order history; sign in as `luna-sparks` for creator/profile/content/shop views; and sign in as `admin` for privileged routes. Use `make smoke` for the basic service check and see [production environment](PRODUCTION_ENVIRONMENT.md) for deployment work.
