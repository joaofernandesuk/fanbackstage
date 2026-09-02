from enum import StrEnum

from fastapi import HTTPException, status

from app.models.identity import User


class Permission(StrEnum):
    ACCOUNT_SELF_READ = "account.self.read"
    ADMIN_ACCESS = "admin.access"
    MODERATION_ACCESS = "moderation.access"
    CREATOR_ACCESS = "creator.access"
    MANAGER_ACCESS = "manager.access"
    FINANCIAL_ACCESS = "financial.access"
    FINANCIAL_REFUND = "financial.refund"
    FINANCIAL_RECONCILE = "financial.reconcile"
    FINANCIAL_AUDIT = "financial.audit"
    FINANCIAL_CONFIGURE = "financial.configure"
    MODERATION_CASE_VIEW = "moderation.case.view"
    MODERATION_CASE_TRIAGE = "moderation.case.triage"
    MODERATION_ACTION = "moderation.action"
    MODERATION_APPEAL_REVIEW = "moderation.appeal.review"
    CONSENT_RELEASE_MANAGE = "consent.release.manage"
    MODERATION_SENSITIVE_EVIDENCE = "moderation.sensitive_evidence"
    COMPLIANCE_VIEW = "compliance.view"
    COMPLIANCE_POLICY_MANAGE = "compliance.policy.manage"
    COMPLIANCE_JURISDICTION_MANAGE = "compliance.jurisdiction.manage"
    COMPLIANCE_VERIFICATION_VIEW = "compliance.verification.view"
    COMPLIANCE_VERIFICATION_REVIEW = "compliance.verification.review"
    COMPLIANCE_PROVIDER_MANAGE = "compliance.provider.manage"
    LEGAL_DOCUMENT_EDIT = "legal.document.edit"
    LEGAL_DOCUMENT_PUBLISH = "legal.document.publish"
    FEATURE_FLAG_MANAGE = "feature_flag.manage"
    SITE_SETTINGS_MANAGE = "site_settings.manage"


ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "viewer": {Permission.ACCOUNT_SELF_READ},
    "creator": {Permission.ACCOUNT_SELF_READ, Permission.CREATOR_ACCESS},
    "manager": {Permission.ACCOUNT_SELF_READ, Permission.MANAGER_ACCESS},
    "moderator": {
        Permission.ACCOUNT_SELF_READ,
        Permission.MODERATION_ACCESS,
        Permission.MODERATION_CASE_VIEW,
        Permission.MODERATION_CASE_TRIAGE,
        Permission.MODERATION_ACTION,
        Permission.MODERATION_APPEAL_REVIEW,
        Permission.CONSENT_RELEASE_MANAGE,
        Permission.COMPLIANCE_VIEW,
        Permission.COMPLIANCE_VERIFICATION_VIEW,
        Permission.COMPLIANCE_VERIFICATION_REVIEW,
    },
    "admin": {
        Permission.ACCOUNT_SELF_READ,
        Permission.ADMIN_ACCESS,
        Permission.MODERATION_ACCESS,
        Permission.FINANCIAL_ACCESS,
        Permission.MODERATION_CASE_VIEW,
        Permission.MODERATION_CASE_TRIAGE,
        Permission.MODERATION_ACTION,
        Permission.MODERATION_APPEAL_REVIEW,
        Permission.CONSENT_RELEASE_MANAGE,
        Permission.COMPLIANCE_VIEW,
        Permission.COMPLIANCE_VERIFICATION_VIEW,
        Permission.COMPLIANCE_VERIFICATION_REVIEW,
        Permission.LEGAL_DOCUMENT_EDIT,
        Permission.SITE_SETTINGS_MANAGE,
    },
    "super_admin": set(Permission),
}


def authorize(actor: User, permission: Permission) -> None:
    granted = {
        permission for role in actor.roles for permission in ROLE_PERMISSIONS.get(role.name, set())
    }
    if permission not in granted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
