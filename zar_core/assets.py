"""Binary asset store. Keeps logos/proofs out of Firestore documents (1 MiB limit)."""
from __future__ import annotations
import base64, hashlib, uuid
from pathlib import Path
import json
from typing import Protocol


class AssetStore(Protocol):
    shared: bool   # True when a separate service can read the same assets
    def put(self, raw: bytes, mime: str, *, tenant_id: str, session_id: str, kind: str) -> str: ...
    def get(self, ref: str) -> tuple[bytes, str]: ...


class MemoryAssets:
    shared = False

    def __init__(self) -> None:
        self._items: dict[str, tuple[bytes, str]] = {}

    def put(self, raw, mime, *, tenant_id, session_id, kind):
        ref = f"mem://{tenant_id}/{session_id}/{kind}/{uuid.uuid4().hex[:10]}"
        self._items[ref] = (raw, mime)
        return ref

    def get(self, ref):
        return self._items[ref]


class GCSAssets:
    shared = True

    def __init__(self, bucket: str) -> None:
        from google.cloud import storage
        if not bucket:
            raise ValueError("ZAR_ASSET_BUCKET is required when ZAR_ASSET_MODE=gcs")
        self.bucket = storage.Client().bucket(bucket)

    def put(self, raw, mime, *, tenant_id, session_id, kind):
        name = f"{tenant_id}/{session_id}/{kind}/{uuid.uuid4().hex[:10]}"
        self.bucket.blob(name).upload_from_string(raw, content_type=mime)
        return f"gs://{self.bucket.name}/{name}"

    def get(self, ref):
        name = ref.split("/", 3)[3]
        blob = self.bucket.blob(name)
        raw = blob.download_as_bytes()
        return raw, blob.content_type or "application/octet-stream"


class FileAssets:
    shared = False

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, raw, mime, *, tenant_id, session_id, kind):
        key = uuid.uuid4().hex
        (self.root / key).write_bytes(raw)
        (self.root / (key + ".json")).write_text(json.dumps({"mime": mime}), encoding="utf-8")
        return f"local://{key}"

    def get(self, ref):
        key = ref.removeprefix("local://")
        if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid asset reference")
        return (self.root / key).read_bytes(), json.loads((self.root / (key + ".json")).read_text())["mime"]


def make_assets(mode: str, bucket: str = "") -> AssetStore:
    from . import config
    if mode == "supabase":
        from .supabase_assets import SupabaseAssets
        return SupabaseAssets(config.SUPABASE_URL, config.SUPABASE_SECRET_KEY, bucket)
    if mode == "gcs": return GCSAssets(bucket)
    if mode == "filesystem": return FileAssets(Path(config.DATA_DIR) / "assets")
    if mode == "memory": return MemoryAssets()
    raise ValueError(f"Unknown asset mode: {mode}")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()
