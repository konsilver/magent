#!/usr/bin/env python3
"""One-time migration: import admin skills from filesystem into PostgreSQL.

Usage (inside container):
    python scripts/migrate_admin_skills_to_db.py [--admin-dir /app/storage/admin_skills]

Skips skills that already exist in the database.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Ensure backend source is on sys.path
_script_dir = Path(__file__).resolve().parent
_repo_root = _script_dir.parent
sys.path.insert(0, str(_repo_root / "src" / "backend"))


def main():
    parser = argparse.ArgumentParser(description="Migrate admin skills from filesystem to DB")
    parser.add_argument(
        "--admin-dir",
        default="/app/storage/admin_skills",
        help="Admin skills directory (default: /app/storage/admin_skills)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing to DB",
    )
    args = parser.parse_args()

    admin_dir = Path(args.admin_dir)
    if not admin_dir.exists():
        print(f"Admin skills directory not found: {admin_dir}")
        sys.exit(0)

    from core.db.engine import SessionLocal
    from core.db.models import AdminSkill
    from agent_skills.registry import _split_frontmatter, _load_skill_metadata_from_str, SkillSpecError

    skill_dirs = [d for d in admin_dir.iterdir() if d.is_dir()]
    if not skill_dirs:
        print("No skill directories found.")
        sys.exit(0)

    db = SessionLocal()
    try:
        migrated = 0
        skipped = 0
        errors = 0

        for skill_dir in sorted(skill_dirs):
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                print(f"  [SKIP] {skill_dir.name}: no SKILL.md")
                skipped += 1
                continue

            raw = skill_file.read_text(encoding="utf-8")

            try:
                fm, _ = _split_frontmatter(raw)
                skill_id = fm.get("name", "").strip() or skill_dir.name
                meta = _load_skill_metadata_from_str(raw, skill_id)
            except (SkillSpecError, Exception) as e:
                print(f"  [ERROR] {skill_dir.name}: {e}")
                errors += 1
                continue

            # Check if already exists
            existing = db.query(AdminSkill).filter(AdminSkill.skill_id == skill_id).first()
            if existing is not None:
                print(f"  [EXISTS] {skill_id} — skipping")
                skipped += 1
                continue

            if args.dry_run:
                print(f"  [DRY-RUN] Would insert: {skill_id} ({meta.name})")
                migrated += 1
                continue

            now = datetime.utcnow()
            row = AdminSkill(
                skill_id=skill_id,
                skill_content=raw,
                display_name=meta.name,
                description=meta.description,
                version=meta.version,
                tags=meta.tags,
                allowed_tools=meta.allowed_tools,
                is_enabled=True,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()
            print(f"  [OK] Inserted: {skill_id} ({meta.name})")
            migrated += 1

        if not args.dry_run:
            db.commit()

        print(f"\nDone. Migrated={migrated}, Skipped={skipped}, Errors={errors}")
        if errors > 0:
            sys.exit(1)

    except Exception as e:
        db.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
