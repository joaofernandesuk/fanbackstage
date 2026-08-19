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
