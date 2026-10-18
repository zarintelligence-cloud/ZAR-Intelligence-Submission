"""Process-wide service handles (store, assets) set once by bootstrap; used by capabilities."""
from __future__ import annotations
import base64
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from .assets import AssetStore
from .store import Store


class Services:
    def __init__(self):
        self._default_store = self._default_assets = None
        self._active = ContextVar("zar_services", default=None)

    @property
    def store(self):
        return self._active.get()[0] if self._active.get() else self._default_store

    @store.setter
    def store(self, value): self._default_store = value

    @property
    def assets(self):
        return self._active.get()[1] if self._active.get() else self._default_assets

    @assets.setter
    def assets(self, value): self._default_assets = value

    @contextmanager
    def use(self, store, assets):
        token = self._active.set((store, assets))
        try: yield
        finally: self._active.reset(token)


services = Services()


def load_binary(payload: dict[str, Any], prefix: str) -> tuple[bytes, str]:
    """Resolve '<prefix>_ref' (asset store) or '<prefix>_b64' (+ '<prefix>_mime')."""
    if payload.get(f"{prefix}_ref"):
        return services.assets.get(payload[f"{prefix}_ref"])
    if payload.get(f"{prefix}_b64"):
        return base64.b64decode(payload[f"{prefix}_b64"]), payload.get(f"{prefix}_mime", "image/png")
    raise ValueError(f"Missing binary payload '{prefix}'")
