"""Read-only/safely-fixable checks before the breaking major upgrade.

Run with the old deployment stopped. By default this emits JSON and never
mutates data. ``--apply-safe-fixes`` only clamps invalid chunk overlap; every
ambiguous configuration still fails the preflight and requires an operator
decision before migration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.db.models import RagMode
from app.schemas.rag_config import parse_rag_config


def _safe_overlap_fix(config: dict[str, Any]) -> bool:
    chunking = config.get("chunking")
    if not isinstance(chunking, dict):
        return False
    params = chunking.get("params")
    if not isinstance(params, dict):
        return False
    strategy = chunking.get("strategy", "fixed_window")
    size_key = "child_chunk_size" if strategy == "parent_child" else "chunk_size"
    size = params.get(size_key)
    overlap = params.get("overlap")
    if not isinstance(size, int) or not isinstance(overlap, int) or size <= 0:
        return False
    if overlap < size:
        return False
    params["overlap"] = min(50, max(0, size // 10), size - 1)
    return True


async def run(*, apply_safe_fixes: bool) -> tuple[dict[str, Any], int]:
    engine = create_async_engine(settings.postgres_url)
    report: dict[str, Any] = {
        "safe_fixes": [],
        "ambiguous_projects": [],
        "missing_objects": [],
        "orphan_objects": [],
    }
    try:
        async with engine.begin() as connection:
            projects = (
                await connection.execute(
                    text("SELECT id, rag_mode, rag_config FROM projects")
                )
            ).mappings()
            for row in projects:
                raw = dict(row["rag_config"] or {})
                try:
                    parse_rag_config(RagMode(row["rag_mode"]), raw)
                    continue
                except Exception as original_error:
                    candidate = json.loads(json.dumps(raw))
                    if _safe_overlap_fix(candidate):
                        try:
                            parse_rag_config(RagMode(row["rag_mode"]), candidate)
                        except Exception:
                            pass
                        else:
                            report["safe_fixes"].append(str(row["id"]))
                            if apply_safe_fixes:
                                await connection.execute(
                                    text(
                                        "UPDATE projects SET rag_config = CAST(:config AS json) "
                                        "WHERE id = :project_id"
                                    ),
                                    {
                                        "config": json.dumps(candidate),
                                        "project_id": row["id"],
                                    },
                                )
                            continue
                    report["ambiguous_projects"].append(
                        {"project_id": str(row["id"]), "error": str(original_error)}
                    )

            documents = (
                (
                    await connection.execute(
                        text("SELECT id, project_id, storage_path FROM documents")
                    )
                )
                .mappings()
                .all()
            )

        from app.services.storage import get_storage_service

        storage = get_storage_service()
        expected = {
            str(row["storage_path"]) for row in documents if row["storage_path"]
        }
        for row in documents:
            path = row["storage_path"]
            if path and not await asyncio.to_thread(storage.file_exists, path):
                report["missing_objects"].append(
                    {"document_id": str(row["id"]), "path": path}
                )
        object_keys = set(await asyncio.to_thread(storage.list_files, "projects/"))
        report["orphan_objects"] = sorted(
            key for key in object_keys if "/raw/" in key and key not in expected
        )
    finally:
        await engine.dispose()

    failed = bool(report["ambiguous_projects"] or report["missing_objects"])
    return report, 2 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-safe-fixes", action="store_true")
    args = parser.parse_args()
    report, exit_code = asyncio.run(run(apply_safe_fixes=args.apply_safe_fixes))
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
