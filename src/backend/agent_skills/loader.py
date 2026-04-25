"""Multi-source skill loader with backend abstraction."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from .backends import CompositeBackend, DatabaseBackend, FilesystemBackend, SkillFileInfo
from .config import get_enabled_skill_sources
from .registry import (
    AgentSkillMetadata,
    AgentSkillSpec,
    _load_skill_from_file,
    _load_skill_from_str,
    _load_skill_metadata_from_file,
    _load_skill_metadata_from_str,
    parse_scripts_json,
)


_SCRIPT_EXTENSIONS = {".py", ".js", ".sh", ".r", ".R"}


def _auto_detect_scripts(
    extra_files: Dict[str, str],
    skill_info: "SkillFileInfo",
) -> List[Dict]:
    """Auto-detect executable scripts when _scripts.json is absent.

    Scans extra_files (DB skills) or the skill directory (filesystem skills)
    for files with known script extensions and returns a whitelist entry for
    each one, so users don't need to manually create _scripts.json.
    """
    scripts: List[Dict] = []

    # Collect candidate filenames
    candidates: List[str] = []

    # From extra_files (DB skills)
    for fname in extra_files:
        if fname.startswith("_"):
            continue
        ext = Path(fname).suffix.lower()
        if ext in _SCRIPT_EXTENSIONS:
            candidates.append(fname)

    # From filesystem (filesystem skills without extra_files)
    if not candidates and skill_info.content is None:
        skill_dir = skill_info.file_path.parent
        for p in skill_dir.iterdir():
            if p.is_file() and not p.name.startswith("_") and p.suffix.lower() in _SCRIPT_EXTENSIONS:
                candidates.append(p.name)

    # Build whitelist entries
    ext_to_lang = {".py": "python", ".js": "javascript", ".sh": "bash", ".r": "r", ".R": "r"}
    for name in sorted(candidates):
        ext = Path(name).suffix.lower()
        scripts.append({
            "name": name,
            "language": ext_to_lang.get(ext, "python"),
            "timeout": 60,
        })

    return scripts


class MultiSourceSkillLoader:
    """Loads skills from multiple sources with priority-based conflict resolution.

    This loader uses the backend abstraction layer to support loading skills from:
    - Built-in skills (agent_skills/skills/)
    - User skills (~/.jingxin-agent/skills/)
    - Project skills (.jingxin/skills/)

    Higher priority sources override lower priority sources for conflicting skill IDs.
    """

    def __init__(self, backend: Optional[CompositeBackend] = None):
        """Initialize the multi-source skill loader.

        Args:
            backend: Optional CompositeBackend. If not provided, creates one from
                     default skill sources.
        """
        if backend is None:
            backend = self._create_default_backend()
        self._backend = backend
        self._metadata_cache: Optional[Dict[str, AgentSkillMetadata]] = None
        # Cache: skill_id → (base_dir_path, materialized_timestamp)
        self._materialized_cache: Dict[str, Tuple[str, float]] = {}

    @staticmethod
    def _create_default_backend() -> CompositeBackend:
        """Create a CompositeBackend from default skill sources.

        Returns:
            CompositeBackend configured with enabled skill sources.
        """
        sources = get_enabled_skill_sources()
        backends = []
        for src in sources:
            if src.name == "admin":
                backends.append(DatabaseBackend(priority=src.priority))
            else:
                backends.append(FilesystemBackend(
                    root_dir=src.root_dir,
                    source_name=src.name,
                    priority=src.priority,
                ))
        return CompositeBackend(backends)

    def load_all_metadata(self) -> Dict[str, AgentSkillMetadata]:
        """Load metadata for all skills (fast, no instructions parsing).

        Uses caching to avoid repeated filesystem scans.

        Returns:
            Dictionary mapping skill_id to AgentSkillMetadata.
        """
        if self._metadata_cache is not None:
            return self._metadata_cache

        metadata_map: Dict[str, AgentSkillMetadata] = {}
        skill_files = self._backend.list_skill_files()

        for skill_info in skill_files:
            try:
                if skill_info.content is not None:
                    metadata = _load_skill_metadata_from_str(skill_info.content, skill_info.skill_id)
                else:
                    metadata = _load_skill_metadata_from_file(skill_info.file_path)
                # Add source information to skill_path for debugging
                metadata_with_source = AgentSkillMetadata(
                    id=metadata.id,
                    name=metadata.name,
                    description=metadata.description,
                    version=metadata.version,
                    tags=metadata.tags,
                    allowed_tools=metadata.allowed_tools,
                    skill_path=f"{skill_info.source_name}:{skill_info.file_path}",
                )
                metadata_map[metadata.id] = metadata_with_source
            except Exception as e:
                # Log warning but continue loading other skills
                print(f"Warning: Failed to load skill metadata from {skill_info.file_path}: {e}")
                continue

        self._metadata_cache = metadata_map
        return metadata_map

    def load_skill_full(self, skill_id: str) -> Optional[AgentSkillSpec]:
        """Load full spec for a single skill by id (on-demand loading).

        Args:
            skill_id: The skill identifier.

        Returns:
            AgentSkillSpec if found, None otherwise.
        """
        # First check if skill exists in metadata
        metadata_map = self.load_all_metadata()
        if skill_id not in metadata_map:
            return None

        # Get the skill file info to find the path
        skill_info = self._backend.get_skill_info(skill_id)
        if skill_info is None:
            return None

        try:
            if skill_info.content is not None:
                spec = _load_skill_from_str(skill_info.content, skill_info.skill_id)
            else:
                spec = _load_skill_from_file(skill_info.file_path)
            # Populate extra_files list from backend
            extra_file_names: List[str] = []
            ef: Dict[str, str] = {}
            try:
                ef = self._backend.get_extra_files(skill_id)
                extra_file_names = sorted(ef.keys())
            except Exception:
                pass

            # Resolve base_dir for {baseDir} substitution
            base_dir = ""
            if skill_info.content is None:
                # Filesystem skill: folder containing SKILL.md
                base_dir = str(skill_info.file_path.parent)
            elif extra_file_names:
                # DB skill with extra files: materialize to cache dir
                # Pass the already-fetched ef dict to avoid a second DB query
                base_dir = self._materialize_skill_files(skill_id, extra_files=ef)

            # Parse _scripts.json if present
            executable_scripts: List[Dict] = []
            scripts_json_content = ef.get("_scripts.json", "")
            if not scripts_json_content and skill_info.content is None:
                # Filesystem skill: check file directly
                scripts_path = skill_info.file_path.parent / "_scripts.json"
                if scripts_path.is_file():
                    scripts_json_content = scripts_path.read_text(encoding="utf-8")
            if scripts_json_content:
                try:
                    executable_scripts = parse_scripts_json(scripts_json_content)
                except Exception as e:
                    logger.warning("Failed to parse _scripts.json for skill '%s': %s", skill_id, e)

            # Auto-generate whitelist from .py files when _scripts.json is absent
            if not executable_scripts:
                auto_scripts = _auto_detect_scripts(ef, skill_info)
                if auto_scripts:
                    executable_scripts = auto_scripts
                    logger.info("Auto-detected %d script(s) for skill '%s': %s",
                                len(auto_scripts), skill_id,
                                [s["name"] for s in auto_scripts])

            # Add source information to skill_path
            spec_with_source = AgentSkillSpec(
                id=spec.id,
                name=spec.name,
                description=spec.description,
                version=spec.version,
                instructions=spec.instructions,
                inputs=spec.inputs,
                outputs=spec.outputs,
                tags=spec.tags,
                allowed_tools=spec.allowed_tools,
                extra_files=extra_file_names,
                base_dir=base_dir,
                examples=spec.examples,
                executable_scripts=executable_scripts,
                skill_path=f"{skill_info.source_name}:{skill_info.file_path}",
            )
            return spec_with_source
        except Exception as e:
            print(f"Warning: Failed to load skill spec for {skill_id}: {e}")
            return None

    def clear_cache(self):
        """Clear the metadata cache (useful for testing or hot-reloading)."""
        self._metadata_cache = None
        self._materialized_cache.clear()

    def get_extra_files(self, skill_id: str) -> Dict[str, str]:
        """Get extra files for a skill.

        Returns:
            {filename: content} dict.
        """
        return self._backend.get_extra_files(skill_id)

    def get_skill_base_dir(self, skill_id: str) -> Optional[str]:
        """Get the base directory for a skill.

        For filesystem skills: returns the actual skill folder path.
        For DB skills: materializes extra_files to a persistent cache directory
        and returns that path.

        Returns:
            Absolute path to the skill's working directory, or None.
        """
        skill_info = self._backend.get_skill_info(skill_id)
        if skill_info is None:
            return None

        # Filesystem skills: the folder containing SKILL.md
        if skill_info.content is None:
            return str(skill_info.file_path.parent)

        # DB skills: materialize to a persistent cache dir
        return self._materialize_skill_files(skill_id)

    def _materialize_skill_files(self, skill_id: str,
                                  extra_files: Optional[Dict[str, str]] = None) -> str:
        """Write DB extra_files to disk so scripts can be executed.

        Uses an in-memory cache to avoid redundant I/O. Files are only
        re-written when the loader is reset (i.e., after admin edits) or
        the cache entry is older than 5 minutes.

        Args:
            skill_id: The skill identifier.
            extra_files: Pre-fetched {filename: content} dict. If None,
                         fetches from backend (saves a DB round-trip when
                         the caller already has the data).

        Returns:
            Absolute path to the materialized directory.
        """
        cache_root = Path.home() / ".cache" / "jingxin-agent" / "skills" / skill_id

        # Check in-memory cache — skip I/O if recently materialized
        if skill_id in self._materialized_cache:
            cached_path, cached_time = self._materialized_cache[skill_id]
            if time.monotonic() - cached_time < 300:  # 5 min TTL
                return cached_path

        cache_root.mkdir(parents=True, exist_ok=True)

        # Write SKILL.md
        skill_info = self._backend.get_skill_info(skill_id)
        if skill_info and skill_info.content:
            (cache_root / "SKILL.md").write_text(skill_info.content, encoding="utf-8")

        # Write extra files (reuse pre-fetched data if available)
        if extra_files is None:
            extra_files = self._backend.get_extra_files(skill_id)
        for filename, content in extra_files.items():
            file_path = cache_root / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        result_path = str(cache_root)
        self._materialized_cache[skill_id] = (result_path, time.monotonic())
        logger.info("Materialized skill '%s' to %s (%d files)",
                     skill_id, cache_root, len(extra_files))
        return result_path

    def get_skill_dir(self, skill_id: str) -> Optional[str]:
        """Get the on-disk directory for a skill.

        For filesystem skills: returns the actual skill folder.
        For DB skills: materializes extra_files to cache and returns that dir.
        This is the path that can be passed to AgentScope's
        ``toolkit.register_agent_skill()``.

        Returns:
            Absolute path to a directory containing SKILL.md, or None.
        """
        skill_info = self._backend.get_skill_info(skill_id)
        if skill_info is None:
            return None

        # Filesystem skill: SKILL.md parent dir
        if skill_info.content is None:
            return str(skill_info.file_path.parent)

        # DB skill: materialize to cache dir so AgentScope can read it
        ef = self._backend.get_extra_files(skill_id)
        return self._materialize_skill_files(skill_id, extra_files=ef)

    def register_skills_to_toolkit(
        self,
        toolkit,
        skill_ids: Optional[List[str]] = None,
    ) -> int:
        """Register skills into an AgentScope Toolkit using its native
        ``register_agent_skill(skill_dir)`` API.

        For each skill:
        - Filesystem skills: pass the existing directory directly.
        - DB skills: materialize to ``~/.cache/jingxin-agent/skills/<id>/``
          first, then register that directory.

        Args:
            toolkit: An ``agentscope.tool.Toolkit`` instance.
            skill_ids: Optional whitelist. If None, registers all skills.

        Returns:
            Number of skills successfully registered.
        """
        metadata = self.load_all_metadata()
        ids_to_register = skill_ids if skill_ids is not None else list(metadata.keys())

        count = 0
        for sid in ids_to_register:
            if sid not in metadata:
                continue
            try:
                skill_dir = self.get_skill_dir(sid)
                if skill_dir is None:
                    continue
                toolkit.register_agent_skill(skill_dir)
                count += 1
            except Exception as e:
                logger.warning("Failed to register skill '%s': %s", sid, e)
        return count

    def get_skill_source(self, skill_id: str) -> Optional[str]:
        """Get the source name for a skill (useful for debugging).

        Args:
            skill_id: The skill identifier.

        Returns:
            Source name (e.g., "built-in", "user", "project") or None if not found.
        """
        skill_info = self._backend.get_skill_info(skill_id)
        return skill_info.source_name if skill_info else None


# Global singleton instance
_global_loader: Optional[MultiSourceSkillLoader] = None


def get_skill_loader(reset: bool = False) -> MultiSourceSkillLoader:
    """Get the global MultiSourceSkillLoader instance.

    Args:
        reset: If True, recreate the loader (useful for testing).

    Returns:
        The global MultiSourceSkillLoader instance.
    """
    global _global_loader
    if _global_loader is None or reset:
        _global_loader = MultiSourceSkillLoader()
    return _global_loader
