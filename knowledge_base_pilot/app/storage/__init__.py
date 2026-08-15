"""Unified storage backend: local disk or S3-compatible (Cloudflare R2)."""
import logging
import mimetypes
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from fastapi import UploadFile
from fastapi.responses import FileResponse, RedirectResponse

logger = logging.getLogger(__name__)

_DEFAULT_LOCAL_DIR = Path(__file__).parent.parent.parent / "knowledge_base"
LOCAL_STORAGE_PATH = Path(os.getenv("LOCAL_STORAGE_PATH", str(_DEFAULT_LOCAL_DIR)))
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")


def _sanitize_filename(filename: str) -> str:
    return os.path.basename(filename)


class StorageBackend(ABC):
    @abstractmethod
    def save_file(
        self,
        file: UploadFile,
        owner_id: int,
        filename: str,
        max_bytes: Optional[int] = None,
    ) -> Path:
        ...

    @abstractmethod
    def ensure_local(self, owner_id: int, filename: str) -> Path:
        """Return a local Path; download first if remote."""
        ...

    @abstractmethod
    def delete_file(self, owner_id: int, filename: str) -> None:
        ...

    @abstractmethod
    def rename_file(self, owner_id: int, old_filename: str, new_filename: str) -> None:
        ...

    @abstractmethod
    def send_file(self, owner_id: int, filename: str) -> Any:
        """Return a FastAPI response (FileResponse or RedirectResponse)."""
        ...

    @abstractmethod
    def exists(self, owner_id: int, filename: str) -> bool:
        """Return True if the object exists in the backing store."""
        ...


class LocalStorage(StorageBackend):
    def _path(self, owner_id: int, filename: str) -> Path:
        path = LOCAL_STORAGE_PATH / str(owner_id) / _sanitize_filename(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def save_file(
        self,
        file: UploadFile,
        owner_id: int,
        filename: str,
        max_bytes: Optional[int] = None,
    ) -> Path:
        path = self._path(owner_id, filename)
        total = 0
        try:
            with path.open("wb") as buffer:
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if max_bytes is not None and total > max_bytes:
                        raise ValueError("File exceeds upload limit")
                    buffer.write(chunk)
        finally:
            file.file.close()
        return path

    def ensure_local(self, owner_id: int, filename: str) -> Path:
        return self._path(owner_id, filename)

    def delete_file(self, owner_id: int, filename: str) -> None:
        path = self._path(owner_id, filename)
        if path.exists():
            path.unlink()

    def rename_file(self, owner_id: int, old_filename: str, new_filename: str) -> None:
        old_path = self._path(owner_id, old_filename)
        new_path = self._path(owner_id, new_filename)
        if not old_path.exists():
            return
        if new_path.exists():
            raise FileExistsError(f"A file named {new_filename} already exists")
        new_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_path), str(new_path))

    def exists(self, owner_id: int, filename: str) -> bool:
        return self._path(owner_id, filename).exists()

    def send_file(self, owner_id: int, filename: str) -> Any:
        path = self.ensure_local(owner_id, filename)
        media_type, _ = mimetypes.guess_type(str(path))
        if not media_type:
            media_type = "application/octet-stream"
        return FileResponse(
            path,
            filename=path.name,
            media_type=media_type,
            content_disposition_type="inline",
        )


class R2Storage(StorageBackend):
    def __init__(self):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is not installed") from exc

        if not all([R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME]):
            raise RuntimeError("R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME must be set")

        self._client = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT_URL,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )

    @property
    def client(self):
        return self._client

    def _key(self, owner_id: int, filename: str) -> str:
        return f"uploads/{owner_id}/{_sanitize_filename(filename)}"

    def _local_path(self, owner_id: int, filename: str) -> Path:
        path = LOCAL_STORAGE_PATH / str(owner_id) / _sanitize_filename(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def save_file(
        self,
        file: UploadFile,
        owner_id: int,
        filename: str,
        max_bytes: Optional[int] = None,
    ) -> Path:
        path = self._local_path(owner_id, filename)
        key = self._key(owner_id, filename)
        total = 0
        try:
            # Stream to a temporary file first so we can enforce the size limit and then upload.
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = Path(tmp.name)
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if max_bytes is not None and total > max_bytes:
                        raise ValueError("File exceeds upload limit")
                    tmp.write(chunk)

            # Persist to local cache and upload to R2
            shutil.move(str(tmp_path), str(path))
            self.client.upload_file(str(path), R2_BUCKET_NAME, key)
        finally:
            file.file.close()
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        return path

    def ensure_local(self, owner_id: int, filename: str) -> Path:
        path = self._local_path(owner_id, filename)
        if path.exists():
            return path
        key = self._key(owner_id, filename)
        try:
            self.client.download_file(R2_BUCKET_NAME, key, str(path))
        except Exception:
            logger.warning("R2 object %s not found or could not be downloaded", key)
        return path

    def delete_file(self, owner_id: int, filename: str) -> None:
        path = self._local_path(owner_id, filename)
        key = self._key(owner_id, filename)
        if path.exists():
            path.unlink()
        try:
            self.client.delete_object(Bucket=R2_BUCKET_NAME, Key=key)
        except Exception:
            logger.exception("Failed to delete R2 object %s", key)

    def rename_file(self, owner_id: int, old_filename: str, new_filename: str) -> None:
        old_key = self._key(owner_id, old_filename)
        new_key = self._key(owner_id, new_filename)
        old_path = self._local_path(owner_id, old_filename)
        new_path = self._local_path(owner_id, new_filename)

        # Check target does not already exist remotely
        try:
            self.client.head_object(Bucket=R2_BUCKET_NAME, Key=new_key)
            raise FileExistsError(f"A file named {new_filename} already exists")
        except Exception as exc:
            if "Not Found" not in str(exc) and "404" not in str(exc):
                raise

        # Copy then delete remote; move local cache
        self.client.copy_object(
            Bucket=R2_BUCKET_NAME,
            Key=new_key,
            CopySource={"Bucket": R2_BUCKET_NAME, "Key": old_key},
        )
        self.client.delete_object(Bucket=R2_BUCKET_NAME, Key=old_key)
        if old_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_path), str(new_path))

    def exists(self, owner_id: int, filename: str) -> bool:
        key = self._key(owner_id, filename)
        try:
            self.client.head_object(Bucket=R2_BUCKET_NAME, Key=key)
            return True
        except Exception:
            return False

    def send_file(self, owner_id: int, filename: str) -> Any:
        key = self._key(owner_id, filename)
        media_type, _ = mimetypes.guess_type(filename)
        if not media_type:
            media_type = "application/octet-stream"

        try:
            url = self.client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": R2_BUCKET_NAME,
                    "Key": key,
                    "ResponseContentDisposition": f"inline; filename={_sanitize_filename(filename)}",
                    "ResponseContentType": media_type,
                },
                ExpiresIn=3600,
            )
            return RedirectResponse(url=url, status_code=307)
        except Exception:
            logger.exception("Failed to generate R2 presigned URL; falling back to streaming")

        # Fallback: download and stream
        path = self.ensure_local(owner_id, filename)
        return FileResponse(
            path,
            filename=_sanitize_filename(filename),
            media_type=media_type,
            content_disposition_type="inline",
        )


_storage: Optional[StorageBackend] = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        backend = os.getenv("STORAGE_BACKEND", "").lower()
        if backend == "local" or (not backend and not R2_ENDPOINT_URL):
            _storage = LocalStorage()
        else:
            _storage = R2Storage()
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
