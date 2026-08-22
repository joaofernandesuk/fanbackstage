from fastapi import APIRouter

from app.api.routes import (
    account,
    admin,
    auth,
    content,
    creators,
    finance,
    groups,
    health,
    marketplace,
    media,
    messaging,
    messaging_moderation,
    referrals,
    social,
    streaming,
    subscriptions,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(referrals.router)
api_router.include_router(account.router)
api_router.include_router(admin.router)
api_router.include_router(creators.router)
api_router.include_router(media.router)
api_router.include_router(marketplace.router)
api_router.include_router(content.router)
api_router.include_router(finance.router)
api_router.include_router(groups.router)
api_router.include_router(subscriptions.router)
api_router.include_router(social.router)
api_router.include_router(messaging.router)
api_router.include_router(messaging_moderation.router)
api_router.include_router(streaming.router)
health_router = health.router
