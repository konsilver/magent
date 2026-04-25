"""Config token verification endpoint."""

from fastapi import APIRouter, Depends

from api.deps import require_config
from core.infra.responses import success_response

router = APIRouter(prefix="/v1/config", tags=["Config"])


@router.get("/verify", dependencies=[Depends(require_config)])
async def verify_config_token():
    """Verify that the provided CONFIG_TOKEN is valid."""
    return success_response(data={"valid": True})
