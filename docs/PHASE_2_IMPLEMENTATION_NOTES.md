# FanBackstage Phase 2 implementation notes

Phase 2 separates private uploaded media from publishable content. `MediaAsset` owns an opaque, private storage key and processing lifecycle; `ContentItem` owns publication, access policy and creator ownership. Galleries keep explicit stable item positions, while standalone video content references one source asset. Neither public schemas nor browser routes expose source keys or permanent object URLs.

Creators request a short-lived, content-type-bound direct upload URL only after server-side approval checks. Finalization verifies object metadata and queues Celery processing. Image processing creates thumbnail, display and blurred-preview derivatives; video processing uses FFprobe/FFmpeg to create poster, preview-clip and playback derivatives. Processing records bounded failures and uses unique asset/derivative constraints so retries do not duplicate derivatives.

The access resolver is authoritative and deny-by-default. Published free content is accessible publicly; owner and moderation roles are privileged; other policies require an active, currently valid `ContentEntitlement`. Changing a content policy never creates, deletes or rewrites entitlement history. Delivery endpoints authorize first and then redirect only to a short-lived derivative URL. Public preview delivery additionally requires published, ready, non-moderated content and the creator's configured preview selection.

Development uses private S3-compatible MinIO. Its CORS policy permits only direct PUT uploads from the configured development web origin; it does not make the bucket or source objects public. Production bucket/CORS policy remains operator-managed.

Known Phase 2 limits: there is no subscription, payment, purchase or entitlement-grant workflow; non-free policies therefore deny until a future legitimate entitlement source exists. There is no adaptive streaming pipeline, full moderation dashboard, or content feed/story product.
