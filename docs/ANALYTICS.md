# Analytics and Event Taxonomy

Analytics are derived from canonical events and must not replace accounting, entitlement or transaction sources of truth.

# 24. Financial Dashboards


## 23.1 Platform admin dashboard

- GMV
- Platform net/gross revenue
- Creator revenue
- Agency revenue
- Pending/available payouts
- Completed payouts
- Refunds
- Chargebacks
- Subscription revenue
- PPV revenue
- Webcam/private revenue
- Messaging revenue
- Marketplace revenue
- Tips
- Featuring/promotion revenue
- Referral/affiliate cost
- Processor fees where known

Filters: today, yesterday, 7 days, 30 days, current month, prior month and custom range; further filter by creator, agency, country/region where lawful, revenue type and currency.


## 23.2 Creator dashboard

- Revenue by source
- Gross versus net
- Pending and available balance
- Payout history
- Profile visits
- Followers/subscribers
- Conversion rate
- Subscription churn/renewal
- ARPU/ARPPU where meaningful
- Top content
- Top customers subject to privacy policy
- Webcam hours and revenue/hour
- Promotion spend and attributable performance


## 23.3 Group dashboard

- Managed creators
- Revenue by creator
- Revenue split by contract version
- Manager activity
- Creator growth/performance
- Group earnings/payout state
- Permission and contract status

# 40. Analytics and Event Taxonomy

Use a consistent event taxonomy from the beginning so product/financial analytics do not depend on scraping application tables later.

- profile_view
- follow_created
- post_published
- post_reaction_created
- post_comment_created
- subscription_checkout_started
- subscription_purchased
- subscription_renewed
- ppv_purchased
- tip_sent
- message_sent
- paid_message_unlocked
- live_joined
- private_requested
- private_started
- marketplace_purchased
- featured_booking_purchased
- creator_group_joined
- creator_group_left
- referral_attributed

Events must not become the authoritative financial record; ledger remains authoritative for money.

## Phase 11 discovery events

Discovery emits bounded, deduplicated `search`, `click`, and `recommendation_impression` rows. They record a request key, safe entity reference where applicable, and the ranking configuration version. Raw search terms are not persisted. These events are neither referral attribution nor a financial source of truth.

## Phase 12 sponsored discovery events

Featuring writes `sponsored_impression`, `sponsored_click`, and
`sponsored_conversion` as distinct, request-deduplicated discovery events. Each
event carries its booking and surface identifiers, so sponsored performance is
never mixed with organic discovery analytics. These events are observational:
they do not alter organic ranking, ledger state, or Phase 10 signup/referral
attribution.

## Phase 14 attribution and export boundary

Referral/acquisition records, organic Discovery interactions, and sponsored
Featuring interactions are reported as independent dimensions. A sponsored
event is explicitly marked and uses the `sponsored_*` event taxonomy, so it
cannot be counted as organic activity; reporting does not write any attribution
or allocation records. Financial referral attribution remains the immutable
ledger-linked allocation created by the referral domain.

CSV exports use the same server-side creator, group, and administrator scope as
their API projections. They are aggregate-only, currency-separated, capped at
50,000 rows, neutralise spreadsheet formula prefixes, and emit an audit event.
They never expose buyer identity, KYC, addresses/shipping/tracking, private
message content, private-live participant data, Trust & Safety evidence, or
protected-media information.

## Phase 14 query-plan review

The financial overview projections restrict `ledger_transactions` to an
`effective_at` range before aggregating immutable ledger entries. The
`ix_ledger_transactions_effective_at` B-tree index supports those bounded time
windows for platform reporting. Creator and group reports first resolve their
small, owner-scoped ledger-account sets through the existing owner indexes and
then use the existing `ledger_entries.ledger_account_id` and
`ledger_entries.transaction_id` join indexes. A speculative composite index was
not added because the current SQL does not predicate a single table on both the
owner and event time.

Discovery-derived analytics restrict `discovery_events` to `created_at` ranges;
`ix_discovery_events_created_at` supports that common bounded scan. Existing
`entity_id` and `actor_user_id` indexes remain the selective scope indexes for
content performance and retention queries. The Phase 14 plan review verified
that these are read-only projections: the only analytics write is the required
append-oriented audit event emitted after an authorised CSV export.
