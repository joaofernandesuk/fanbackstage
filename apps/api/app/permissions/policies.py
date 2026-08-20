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
    FINANCIAL_CONFIGURE = "financial.configure"


ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "viewer": {Permission.ACCOUNT_SELF_READ},
    "creator": {Permission.ACCOUNT_SELF_READ, Permission.CREATOR_ACCESS},
    "manager": {Permission.ACCOUNT_SELF_READ, Permission.MANAGER_ACCESS},
    "moderator": {Permission.ACCOUNT_SELF_READ, Permission.MODERATION_ACCESS},
    "admin": {
        Permission.ACCOUNT_SELF_READ,
        Permission.ADMIN_ACCESS,
        Permission.MODERATION_ACCESS,
        Permission.FINANCIAL_ACCESS,
    },
    "super_admin": set(Permission),
}


def authorize(actor: User, permission: Permission) -> None:
    granted = {
        permission for role in actor.roles for permission in ROLE_PERMISSIONS.get(role.name, set())
    }
    if permission not in granted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
