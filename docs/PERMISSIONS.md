# Roles, Permissions and Delegation

Server-side authorisation rules for users, creators, managers, moderators and administrators.

## Phase 15 notifications

Users may read and change only their own in-app notifications and optional preferences.
Provider delivery events require the server-configured webhook secret; no client may post
delivery state. Notification targets are internal paths only.

# 2. Roles, Identity and Account Model

| Role | Capabilities |
| --- | --- |
| Viewer / Fan | Discover creators; follow; subscribe; buy PPV; tip; message; join live/private sessions; purchase marketplace items; refer users. |
| Creator / Model | Publish and monetise content; stream; sell products; blog; manage subscribers; receive tips; use promotions; join/leave groups. |
| Group / Agency Manager | Manage authorised creator profiles, content, messaging, schedules and analytics within explicit delegated permissions. |
| Moderator | Review reports, content queues, sanctions and verification tasks without unrestricted financial/admin powers. |
| Admin | Platform operations, support, content review, user access, settings and reporting according to permission scope. |
| Super Admin | Restricted high-trust role for platform-wide configuration, permissions, finance and critical operations. |

```text
User
 ├── ViewerProfile
 ├── CreatorProfile
 ├── ManagerProfile
 └── RoleAssignments / Permissions
```

Authentication should be account-centric. Role checks must be enforced server-side. Front-end hiding is never sufficient authorisation.

Registration requires an explicit 18+ self-attestation. Legacy accounts may authenticate
to acknowledge the current policy, but cannot start a paid action, receive a live token,
or access adult-restricted media until they do. This baseline attestation never creates an
entitlement and must not be described as legal age or identity verification.

The central jurisdiction resolver may require provider-backed age assurance before registration or a protected feature. An anonymous verification can be attached to exactly one newly created or authenticated account, but it does not grant login or entitlement. Frontend gates only explain the server decision.

Only `moderation.access` may classify a media asset as `safe_public` or
`adult_restricted`; the domain service enforces the capability, row-locks the asset, and
audits only an actual state change. Creator KYC state is not editable by creators or group
managers. Public serving uses the latest verification outcome and fails closed when it is
not currently verified and adult-confirming.

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

# 36. Permissions Model

Use capability-based permissions with object scope where needed. Group-manager permission is not equivalent to creator ownership.

| Capability example | Creator | Group Manager | Moderator | Admin |
| --- | --- | --- | --- | --- |
| Edit creator bio | Own profile | Only if delegated | No | Support/admin scope |
| Change creator payout destination | Protected self flow | No | No | Restricted support flow |
| Upload content | Own | If delegated | No | Exceptional moderation/support only |
| Reply to creator messages | Own | If delegated | No | Exceptional support policy |
| Change revenue split | Accept/reject proposal | Propose only | No | View/exception workflow |
| Moderate reported content | No | No | Yes | Yes |
| Change platform commission | No | No | No | Restricted admin permission |

Phase 5 feed commands are server-authorized: only an approved creator owns its posts/settings, viewers can react/comment only after post access resolves, and public follows target only approved public creators.

Phase 5 Story commands are likewise server-authorized. Only an approved creator may create a Story, the selected ready media asset must belong to that creator, and it must not already be governed by a content/post/message/marketplace delivery context. Creation requires a creator-scoped idempotency key. A creator may list and soft-delete only their own Stories. Consumer rail, detail, and media delivery fail closed for expired/deleted/removed Stories, two-way blocks, non-public or non-approved creators, unsafe/unready media, and unmet follower/subscription access. Authenticated consumers can file deduplicated Story reports through the existing social-report boundary; moderation access can contain the exact Story with a replay-safe audited removal, after which even owner media delivery is denied. Moderation roles may otherwise inspect eligible active Stories through the existing role boundary, but do not gain original-media URLs. Group-manager Story publication is not implemented without an explicit delegated capability.

## Compliance and legal permissions

| Capability | Moderator | Admin | Super admin |
| --- | --- | --- | --- |
| `compliance.view` | Yes | Yes | Yes |
| `compliance.verification.view` | Yes | Yes | Yes |
| `compliance.verification.review` | Yes | Yes | Yes |
| `compliance.policy.manage` | No | No | Yes |
| `compliance.jurisdiction.manage` | No | No | Yes |
| `compliance.provider.manage` | No | No | Yes |
| `feature_flag.manage` | No | No | Yes |
| `legal.document.edit` | No | Yes | Yes |
| `legal.document.publish` | No | No | Yes |
| `site_settings.manage` | No | Yes | Yes |

Policy/jurisdiction/provider/feature and legal publication are deliberately stronger than ordinary admin access. Verification review is bounded by the current policy threshold/assurance and a finite maximum expiry; it cannot invent a permanent approval. Every state-changing operation requires server authorization and an audit reason where applicable.

Compliance decisions are not role bypasses. Moderator/admin access to operational records does not allow restricted-media viewing, purchasing, Live participation, or entitlement unless a separately defined, audited evidence workflow authorizes it. Admin impersonation must use the target user's ordinary compliance decision and cannot silently elevate it.
# Phase 3 financial permissions

Financial inspection requires `financial.access`; changing the platform commission requires `financial.configure` and remains limited to `super_admin`. Buyer history is limited to the authenticated buyer, and creator balances are limited to the approved creator who owns them.

# Phase 11 discovery permissions

Safe public discovery is anonymous. Server-side eligibility excludes non-public/pending/suspended creators, moderated content/listings, ended or suspended rooms, manual hides, and both directions of a signed-in user's block relationship. Discovery cannot grant access: locked cards remain subject to existing content and media resolvers. Discovery configuration and manual hide actions require `admin.access` and emit audit events.

# Phase 12 featuring permissions

Target eligibility, owner identity, availability and payment initiation are
server-resolved. A creator may book only an eligible target they own. A manager
needs an active creator-scoped `manage_featuring` delegation and may choose only
the manager or target creator as an explicit payer; a manager can never silently
charge a creator. Booking history is visible only to its payer, actor, target
owner, or authorised platform administrator. Surface, slot, price and lifecycle
reconciliation operations require server-side admin authorisation and emit audit
events; UI visibility is not authorisation.
