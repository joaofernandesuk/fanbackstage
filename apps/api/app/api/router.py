from fastapi import APIRouter

from app.api.routes import account, admin, auth, creators, health

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(account.router)
api_router.include_router(admin.router)
api_router.include_router(creators.router)
health_router = health.router
