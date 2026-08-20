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
