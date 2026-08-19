from fastapi import APIRouter

from app.api.deps import CurrentIdentity
from app.permissions.policies import Permission, authorize
from app.schemas.auth import MessageResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/foundation", response_model=MessageResponse)
async def foundation(identity: CurrentIdentity) -> MessageResponse:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    return MessageResponse(message="FanBackstage admin foundation")
