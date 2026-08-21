# Phase 7 implementation notes

Private sessions use server-persisted participant state; browser timers never determine billable use. When any required creator/viewer participant disconnects, the session enters `reconnecting`, accumulated active seconds are frozen, and billing pauses immediately. All required participants must reconnect within the configurable 30-second default grace period before a new active interval can begin. Grace expiry ends the session and settlement uses only already accumulated billable seconds. Provider disconnect/reconnect events are idempotent.

For 2-to-1, one requesting viewer is the sole payer, one specifically identified authenticated viewer is invited, and the creator's separate 2-to-1 rate is snapshotted. The second viewer has a session/user-scoped short-lived token but no financial responsibility. The session cannot activate or bill until creator, payer, invited viewer, and payment authorization are all present. There is one timer and one settlement; split-payer billing and implicit conversion to 1-to-1 are deferred.

Settlement rounds in integer minor units as `ceil(rate_minor * authoritative_seconds / 60)`, applies the stored minimum once, and never exceeds the stored authorization cap. Permanent tests cover 1, 30, 60, 61 and 95 second intervals, reconnect exclusion, replay-safe participant events, and idempotent final ledger settlement. A session that never reaches ACTIVE is cancelled without a minimum charge.

The LiveKit client receives only API-issued short-lived tokens. Private tokens are session- and user-scoped; uninvited users cannot obtain one. Public viewer tokens cannot publish. Public recording is an egress foundation only; private recording is disabled by design.
