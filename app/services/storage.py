"""File storage. Local disk in this version: public images under var/public (served at /media),
private course material under var/private (served only through short-lived signed URLs, EF-52).
The interface is small so an S3 implementation can replace it without touching callers."""

import re
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import sign_path

IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/svg+xml": ".svg"}


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return name[:120] or "file"


async def _write(upload: UploadFile, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    with dest.open("wb") as fh:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                fh.close()
                dest.unlink(missing_ok=True)
                raise AppError("file_too_large", 413)
            fh.write(chunk)
    return size


async def save_public_image(upload: UploadFile) -> str:
    ext = IMAGE_TYPES.get(upload.content_type or "")
    if not ext:
        raise AppError("validation_error", 422, details=[{"field": "file", "message": "Unsupported image type"}])
    key = f"images/{uuid.uuid4().hex}{ext}"
    await _write(upload, settings.public_dir / key)
    return f"/media/{key}"


async def save_private_file(upload: UploadFile, prefix: str) -> tuple[str, int]:
    key = f"{prefix}/{uuid.uuid4().hex}-{_safe_name(upload.filename or 'file')}"
    size = await _write(upload, settings.private_dir / key)
    return key, size


def private_path(key: str) -> Path:
    path = (settings.private_dir / key).resolve()
    if settings.private_dir.resolve() not in path.parents:
        raise AppError("not_found", 404)
    return path


def signed_download_url(key: str) -> str:
    # Relative URL: served through the same origin as the site (Next.js proxies /api).
    return sign_path(f"/api/v1/files/{key}")


def delete_private(key: str | None) -> None:
    if key:
        private_path(key).unlink(missing_ok=True)
