from __future__ import annotations
from collections.abc import Callable
from .contracts import CapabilityRequest, CapabilityResult

CapabilityFn = Callable[[CapabilityRequest], CapabilityResult]


class CapabilityRegistry:
    """Routes (domain, capability) to a provider. '*' registers a cross-domain provider."""

    def __init__(self) -> None:
        self._fns: dict[tuple[str, str], CapabilityFn] = {}
        self._providers: dict[tuple[str, str], str] = {}

    def register(self, domain: str, name: str, fn: CapabilityFn, provider: str = "") -> None:
        key = (domain, name)
        if key in self._fns:
            raise ValueError(f"Duplicate capability {key}")
        self._fns[key] = fn
        self._providers[key] = provider or getattr(fn, "__module__", "")

    def resolve(self, domain: str, name: str) -> CapabilityFn | None:
        return self._fns.get((domain, name)) or self._fns.get(("*", name))

    def invoke(self, req: CapabilityRequest) -> CapabilityResult:
        fn = self.resolve(req.context.domain, req.capability)
        if not fn:
            return CapabilityResult(request_id=req.request_id, capability=req.capability,
                                    status="error", error="Capability not registered")
        try:
            return fn(req)
        except Exception as exc:  # structured error, never a stack trace to callers
            return CapabilityResult(request_id=req.request_id, capability=req.capability,
                                    status="error", error=f"{type(exc).__name__}: {exc}")

    def describe(self) -> list[dict[str, str]]:
        return sorted(({"domain": d, "capability": c, "provider": self._providers[(d, c)]}
                       for d, c in self._fns), key=lambda x: (x["domain"], x["capability"]))
