# Historical Python Formatting Debt

The release gate keeps `ruff check` enabled for the complete API repository.
Formatting is intentionally enforced against every Python file changed since
`v0.9.0-groups-agencies`, so Phase 9 cannot add unformatted code without
rewriting previously tagged history solely for style.

As of Phase 9, these pre-existing files remain outside Ruff format compliance:

- `apps/api/alembic/versions/20260820_0008_social_feed.py`
- `apps/api/alembic/versions/20260820_0009_feed_announcement_override.py`
- `apps/api/alembic/versions/20260821_0013_group_ledger_accounts.py`
- `apps/api/app/api/routes/health.py`
- `apps/api/app/api/routes/social.py`
- `apps/api/app/models/social.py`
- `apps/api/app/schemas/social.py`
- `apps/api/app/social/service.py`
- `apps/api/app/streaming/service.py`
- `apps/api/tests/test_social_feed.py`
- `apps/api/tests/test_streaming.py`

They must be remediated in a separately scoped maintenance change. No blanket
Ruff exclusion is used.
