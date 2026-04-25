"""Configuration for multi-source skill loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class SkillSourceConfig:
    """Configuration for a skill source."""

    name: str  # Human-readable name (e.g., "built-in", "user", "project")
    root_dir: Path  # Root directory containing skill folders
    priority: int  # Priority for conflict resolution (higher = higher priority)
    enabled: bool = True  # Whether this source is enabled


def get_default_skill_sources() -> List[SkillSourceConfig]:
    """Get default skill source configurations.

    Priority levels:
    - Built-in (priority=0): agent_skills/skills/
    - Admin (priority=75): /app/storage/admin_skills/
    - User (priority=50): ~/.jingxin-agent/skills/
    - Project (priority=100): .jingxin/skills/

    Environment variables:
    - JINGXIN_ADMIN_SKILLS_DIR: Override admin skills directory
    - JINGXIN_USER_SKILLS_DIR: Override user skills directory
    - JINGXIN_PROJECT_SKILLS_DIR: Override project skills directory
    - JINGXIN_DISABLE_ADMIN_SKILLS: Disable admin skills (set to "1" or "true")
    - JINGXIN_DISABLE_USER_SKILLS: Disable user skills (set to "1" or "true")
    - JINGXIN_DISABLE_PROJECT_SKILLS: Disable project skills (set to "1" or "true")

    Returns:
        List of SkillSourceConfig in priority order (lowest to highest).
    """
    sources: List[SkillSourceConfig] = []

    # 1. Built-in skills (always enabled)
    builtin_dir = Path(__file__).parent / "skills"
    sources.append(
        SkillSourceConfig(
            name="built-in",
            root_dir=builtin_dir.resolve(),
            priority=0,
            enabled=True,
        )
    )

    # 2. Admin skills (managed via admin backend)
    admin_skills_dir = os.getenv(
        "JINGXIN_ADMIN_SKILLS_DIR",
        "/app/storage/admin_skills/",
    )
    admin_disabled = os.getenv("JINGXIN_DISABLE_ADMIN_SKILLS", "").lower() in (
        "1",
        "true",
        "yes",
    )
    sources.append(
        SkillSourceConfig(
            name="admin",
            root_dir=Path(admin_skills_dir).expanduser().resolve(),
            priority=75,
            enabled=not admin_disabled,
        )
    )

    # 3. User skills
    user_skills_dir = os.getenv(
        "JINGXIN_USER_SKILLS_DIR",
        "~/.jingxin-agent/skills",
    )
    user_disabled = os.getenv("JINGXIN_DISABLE_USER_SKILLS", "").lower() in (
        "1",
        "true",
        "yes",
    )
    sources.append(
        SkillSourceConfig(
            name="user",
            root_dir=Path(user_skills_dir).expanduser().resolve(),
            priority=50,
            enabled=not user_disabled,
        )
    )

    # 4. Project skills
    project_skills_dir = os.getenv(
        "JINGXIN_PROJECT_SKILLS_DIR",
        ".jingxin/skills",
    )
    project_disabled = os.getenv("JINGXIN_DISABLE_PROJECT_SKILLS", "").lower() in (
        "1",
        "true",
        "yes",
    )
    sources.append(
        SkillSourceConfig(
            name="project",
            root_dir=Path(project_skills_dir).expanduser().resolve(),
            priority=100,
            enabled=not project_disabled,
        )
    )

    return sources


def get_enabled_skill_sources() -> List[SkillSourceConfig]:
    """Get only enabled skill sources.

    Returns:
        List of enabled SkillSourceConfig in priority order.
    """
    return [src for src in get_default_skill_sources() if src.enabled]
