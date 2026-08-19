# Groups, Agencies and Revenue Splits

Versioned creator/group contracts, delegated permissions, exits and immutable historical split rules.

# 21. Group / Agency Management

Group management is a core contract and permission system, not a simple foreign key from creator to agency.


## 20.1 Core entities

```text
Group
GroupMember
GroupInvitation
GroupContract
GroupContractVersion
RevenueSplit
GroupPermissionGrant
```


## 20.2 Joining a group

A creator receives a clear invitation showing the financial split and delegated permissions. Acceptance creates a versioned contract record with an effective date and immutable accepted terms.


## 20.3 Immutable historical split rule

If a creator joins at 50/50 and the group later changes its default to 30/70, that creator remains on 50/50. The new default applies only to future contracts unless the existing creator explicitly accepts a new contract version.

```text
GroupContractVersion
creator_id
group_id
creator_percentage = 50
group_percentage = 50
effective_from
effective_until
version = 1
status = active
accepted_at
accepted_by_creator_id
```


## 20.4 Contract changes

A manager can propose revised terms, but the current contract remains active until the creator accepts. A rejected or expired proposal has no effect on settlement.


## 20.5 Leaving a group

- Creator must have a supported exit mechanism subject only to lawful/explicit contract policy.
- Creator-owned content remains associated with the creator.
- Historical earnings retain the split that applied when earned.
- Future eligible earnings stop using the group split after the effective exit time.
- Manager access is revoked according to exit state.
- Ownership of agency-created drafts/assets must be defined explicitly before implementation.
- Scheduled posts, message access, analytics history and marketplace fulfilment need deterministic transition rules.


## 20.6 Delegated permissions

- Edit profile
- Upload/manage content
- Create/schedule posts
- Reply to messages
- Manage webcam settings/schedule
- Manage pricing/promotions
- Run featured placements
- View analytics
- Manage marketplace
- Manage calendar
- Full-management preset or custom grants


## 20.7 Prohibited unilateral manager actions

- Take ownership of creator identity/account.
- Prevent supported group exit by changing application data.
- Change creator payout destination without protected creator-authorised workflow.
- Change identity/KYC data.
- Delete creator account without appropriate creator/admin process.
- Retroactively change an accepted revenue split.
