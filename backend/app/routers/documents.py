"""Document upload into OpenViking resources."""

from __future__ import annotations

import os
import shutil
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from ..config import Settings, get_settings
from ..db import Database, new_id
from ..deps import get_current_user, get_db
from ..viking import documents_root, slugify, user_client

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _save_upload(upload: UploadFile, dest: str, max_bytes: int) -> int:
    size = 0
    with open(dest, "wb") as fh:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                fh.close()
                os.remove(dest)
                raise HTTPException(status_code=413, detail="File too large")
            fh.write(chunk)
    return size


@router.post("", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    filename = os.path.basename(file.filename or "upload.bin")
    stem, ext = os.path.splitext(filename)
    slug = f"{slugify(stem)}-{new_id()[:8]}"
    staging_dir = os.path.join(settings.uploads_path, user["id"], slug)
    os.makedirs(staging_dir, exist_ok=True)
    staged_path = os.path.join(staging_dir, filename)
    size = await run_in_threadpool(_save_upload, file, staged_path, settings.max_upload_mb * 1024 * 1024)

    target_uri = f"{documents_root(user['id'])}/{slug}"
    try:
        async with user_client(settings, user["id"]) as client:
            result = await client.add_resource(
                staged_path,
                to=target_uri,
                wait=False,
                options={"reason": f"Uploaded by {user['username']} via {settings.app_name}", "create_parent": True},
            )
    except Exception as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise HTTPException(status_code=502, detail=f"OpenViking rejected the upload: {exc}") from exc
    finally:
        # The SDK uploads the bytes to OpenViking; the local copy is only a staging area.
        shutil.rmtree(staging_dir, ignore_errors=True)

    task_id = None
    status = "processing"
    if isinstance(result, dict):
        task_id = result.get("task_id") or (result.get("task") or {}).get("task_id")
        status = str(result.get("status") or status)
        target_uri = result.get("root_uri") or result.get("uri") or target_uri
    return db.create_document(user["id"], filename, target_uri, size, status, task_id)


@router.get("")
async def list_documents(
    refresh: bool = False,
    user: dict[str, Any] = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    docs = db.list_documents(user["id"])
    if refresh:
        pending = [d for d in docs if d.get("task_id") and d["status"] not in ("completed", "failed")]
        if pending:
            try:
                async with user_client(settings, user["id"]) as client:
                    for doc in pending:
                        task = await client.get_task(doc["task_id"])
                        new_status = (task or {}).get("status")
                        if new_status and new_status != doc["status"]:
                            db.update_document_status(doc["id"], str(new_status))
                            doc["status"] = str(new_status)
            except Exception:
                pass
    return docs


@router.get("/{doc_id}")
async def get_document(
    doc_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    doc = db.get_document(doc_id, user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    overview = None
    try:
        async with user_client(settings, user["id"]) as client:
            overview = await client.overview(doc["uri"])
    except Exception as exc:
        overview = f"(overview not available yet: {exc})"
    return {**doc, "overview": overview}


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    doc = db.get_document(doc_id, user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        async with user_client(settings, user["id"]) as client:
            await client.rm(doc["uri"], recursive=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenViking delete failed: {exc}") from exc
    db.delete_document(doc_id)
