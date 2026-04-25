"""User management API routes (v1)."""

from typing import Optional, List
from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from core.db.engine import get_db
from core.auth.backend import get_current_user, UserContext
from core.db.repository import UserRepository, CatalogRepository
from core.infra.responses import success_response
from core.infra.exceptions import ResourceNotFoundError, AccessDeniedError

router = APIRouter(prefix="/v1", tags=["Users"])


# Request/Response Models
class UserPreferences(BaseModel):
    """User preferences model."""
    default_model: Optional[str] = Field("gpt-4", description="Default AI model")
    language: Optional[str] = Field("zh-CN", description="Preferred language")
    theme: Optional[str] = Field("auto", description="UI theme: light, dark, auto")
    enabled_skills: Optional[List[str]] = Field(default_factory=list, description="Enabled skill IDs")
    enabled_mcps: Optional[List[str]] = Field(default_factory=list, description="Enabled MCP server IDs")


class UserResponse(BaseModel):
    """User information response model."""
    user_id: str
    username: str
    email: Optional[str] = None
    avatar: Optional[str] = None
    created_at: str


@router.get("/me", summary="获取当前用户信息")
async def get_current_user_info(
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get current authenticated user information.

    Returns the user profile including:
    - user_id: Internal user ID
    - username: Display name
    - email: Email address (if available)
    - avatar: Avatar URL (if available)
    - created_at: Account creation time
    """
    user_repo = UserRepository(db)
    user_shadow = user_repo.get_by_id(user.user_id)

    if not user_shadow:
        raise ResourceNotFoundError(
            resource_type="user",
            resource_id=user.user_id
        )

    data = {
        "user_id": user_shadow.user_id,
        "username": user_shadow.username,
        "email": user_shadow.email,
        "avatar": user_shadow.avatar_url,
        "created_at": user_shadow.created_at.isoformat()
    }

    return success_response(
        data=data,
        message="User information retrieved successfully"
    )


@router.get("/users/{user_id}/preferences", summary="获取用户偏好设置")
async def get_user_preferences(
    user_id: str = Path(..., description="User ID"),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get user preference settings.

    Users can only access their own preferences.
    Preferences include:
    - default_model: Default AI model selection
    - language: Preferred language
    - theme: UI theme preference
    - enabled_skills: List of enabled skill IDs
    - enabled_mcps: List of enabled MCP server IDs
    """
    # Check if user is accessing their own preferences
    if user_id != user.user_id:
        raise AccessDeniedError(
            message="Access denied",
            reason="Users can only access their own preferences"
        )

    user_repo = UserRepository(db)
    catalog_repo = CatalogRepository(db)

    # Get user shadow
    user_shadow = user_repo.get_by_id(user_id)
    if not user_shadow:
        raise ResourceNotFoundError(
            resource_type="user",
            resource_id=user_id
        )

    # Get metadata preferences
    metadata = user_shadow.extra_data or {}
    preferences = metadata.get("preferences", {})

    # Get enabled skills and MCPs from catalog overrides
    overrides = catalog_repo.list_overrides(user_id)
    enabled_skills = [
        o.item_id for o in overrides
        if o.kind == "skill" and o.enabled
    ]
    enabled_mcps = [
        o.item_id for o in overrides
        if o.kind == "mcp" and o.enabled
    ]

    # Build preferences response
    data = {
        "default_model": preferences.get("default_model", "gpt-4"),
        "language": preferences.get("language", "zh-CN"),
        "theme": preferences.get("theme", "auto"),
        "enabled_skills": enabled_skills,
        "enabled_mcps": enabled_mcps
    }

    return success_response(
        data=data,
        message="User preferences retrieved successfully"
    )


@router.put("/users/{user_id}/preferences", summary="更新用户偏好设置")
async def update_user_preferences(
    preferences: UserPreferences,
    user_id: str = Path(..., description="User ID"),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update user preference settings.

    Users can only update their own preferences.
    All fields are optional - only provided fields will be updated.

    Note: enabled_skills and enabled_mcps are stored in catalog_overrides table
    and will be synchronized accordingly.
    """
    # Check if user is updating their own preferences
    if user_id != user.user_id:
        raise AccessDeniedError(
            message="Access denied",
            reason="Users can only update their own preferences"
        )

    user_repo = UserRepository(db)
    catalog_repo = CatalogRepository(db)

    # Get user shadow
    user_shadow = user_repo.get_by_id(user_id)
    if not user_shadow:
        raise ResourceNotFoundError(
            resource_type="user",
            resource_id=user_id
        )

    # Update metadata preferences
    metadata = user_shadow.extra_data or {}
    if "preferences" not in metadata:
        metadata["preferences"] = {}

    # Update preference fields
    if preferences.default_model is not None:
        metadata["preferences"]["default_model"] = preferences.default_model
    if preferences.language is not None:
        metadata["preferences"]["language"] = preferences.language
    if preferences.theme is not None:
        metadata["preferences"]["theme"] = preferences.theme

    # Save metadata
    user_repo.update(user_id, {"extra_data": metadata})

    # Update catalog overrides for skills and MCPs
    if preferences.enabled_skills is not None:
        # Get all existing skill overrides
        existing_skills = catalog_repo.list_overrides(user_id, kind="skill")
        existing_skill_ids = {o.item_id for o in existing_skills}

        # Update enabled status for all skills
        for skill_id in preferences.enabled_skills:
            catalog_repo.upsert_override(
                user_id=user_id,
                kind="skill",
                item_id=skill_id,
                enabled=True
            )

        # Disable skills that are not in the enabled list
        for skill in existing_skills:
            if skill.item_id not in preferences.enabled_skills:
                catalog_repo.upsert_override(
                    user_id=user_id,
                    kind="skill",
                    item_id=skill.item_id,
                    enabled=False
                )

    if preferences.enabled_mcps is not None:
        # Get all existing MCP overrides
        existing_mcps = catalog_repo.list_overrides(user_id, kind="mcp")
        existing_mcp_ids = {o.item_id for o in existing_mcps}

        # Update enabled status for all MCPs
        for mcp_id in preferences.enabled_mcps:
            catalog_repo.upsert_override(
                user_id=user_id,
                kind="mcp",
                item_id=mcp_id,
                enabled=True
            )

        # Disable MCPs that are not in the enabled list
        for mcp in existing_mcps:
            if mcp.item_id not in preferences.enabled_mcps:
                catalog_repo.upsert_override(
                    user_id=user_id,
                    kind="mcp",
                    item_id=mcp.item_id,
                    enabled=False
                )

    return success_response(
        message="User preferences updated successfully"
    )
