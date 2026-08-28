import pytest
from fastapi import HTTPException

from app.models.identity import Role, User
from app.permissions.policies import Permission, authorize


def test_viewer_cannot_access_admin() -> None:
    user = User(
        email="fan@example.com",
        password_hash="not-a-real-password",
        roles=[Role(name="viewer", description="")],
    )
    with pytest.raises(HTTPException) as error:
        authorize(user, Permission.ADMIN_ACCESS)
    assert error.value.status_code == 403


def test_user_can_hold_multiple_roles_without_duplicate_identity() -> None:
    user = User(
        email="creator@example.com",
        password_hash="not-a-real-password",
        roles=[Role(name="viewer", description=""), Role(name="creator", description="")],
    )
    authorize(user, Permission.CREATOR_ACCESS)
    assert user.email == "creator@example.com"


def test_compliance_and_legal_permissions_remain_granular() -> None:
    moderator = User(
        email="moderator@example.com",
        password_hash="not-a-real-password",
        roles=[Role(name="moderator", description="")],
    )
    admin = User(
        email="admin@example.com",
        password_hash="not-a-real-password",
        roles=[Role(name="admin", description="")],
    )
    super_admin = User(
        email="super-admin@example.com",
        password_hash="not-a-real-password",
        roles=[Role(name="super_admin", description="")],
    )

    authorize(moderator, Permission.COMPLIANCE_VERIFICATION_REVIEW)
    with pytest.raises(HTTPException):
        authorize(moderator, Permission.COMPLIANCE_POLICY_MANAGE)
    with pytest.raises(HTTPException):
        authorize(moderator, Permission.LEGAL_DOCUMENT_EDIT)

    authorize(admin, Permission.LEGAL_DOCUMENT_EDIT)
    authorize(admin, Permission.SITE_SETTINGS_MANAGE)
    with pytest.raises(HTTPException):
        authorize(admin, Permission.LEGAL_DOCUMENT_PUBLISH)
    with pytest.raises(HTTPException):
        authorize(admin, Permission.COMPLIANCE_PROVIDER_MANAGE)

    for permission in Permission:
        authorize(super_admin, permission)
