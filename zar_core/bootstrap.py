"""Builds a runtime from configuration. Same code for monolith, Polaris and workshop roles."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from . import __version__, config
from .assets import make_assets
from .domain_loader import DomainCatalog
from .invoker import LocalInvoker, RemoteInvoker, A2AInvoker
from .registry import CapabilityRegistry
from .release_registry import ComponentVersion, ReleaseRegistry
from .runtime import ZARRuntime
from .services import services
from .store import make_store

ROOT = Path(__file__).resolve().parents[1]
CORE_COMPONENTS = [("zar-core", __version__, "core", {}),
                   ("contract-schema", "3.1.0", "schema", {"zar-core": "3"}),
                   ("workflow-runtime", "1.1.0", "runtime", {"zar-core": "3"}),
                   ("policy-runtime", "1.1.0", "runtime", {"zar-core": "3"}),
                   ("decision-runtime", "1.1.0", "runtime", {"zar-core": "3"})]


@dataclass
class App:
    registry: CapabilityRegistry | None
    catalog: DomainCatalog
    releases: ReleaseRegistry
    runtime: ZARRuntime


def build(role: str = "monolith", *, domains: list[str] | None = None,
          invoke_mode: str | None = None) -> App:
    invoke_mode = invoke_mode or config.INVOKE_MODE
    if config.LLM_MODE not in {"mock", "gemini"}:
        raise ValueError("ZAR_LLM_MODE must be mock or gemini")
    if invoke_mode not in {"local", "remote", "a2a"}:
        raise ValueError("ZAR_INVOKE_MODE must be local, remote or a2a")
    if invoke_mode in {"remote", "a2a"} and (config.STORE_MODE not in {"firestore", "supabase"} or config.ASSET_MODE != "gcs"):
        raise ValueError("Remote mode requires shared Firestore or Supabase and GCS backends")
    services.store = make_store(config.STORE_MODE)
    services.assets = make_assets(config.ASSET_MODE, config.ASSET_BUCKET)
    hosts_capabilities = role == "workshop" or invoke_mode == "local"
    registry = CapabilityRegistry() if hosts_capabilities else None
    catalog, releases = DomainCatalog(), ReleaseRegistry(config.RELEASE_ID)
    for name, version, kind, requires in CORE_COMPONENTS:
        releases.register(ComponentVersion(name, version, kind, requires))
    for d in domains or config.DOMAINS:
        info = catalog.load(ROOT / "domains" / d, registry).info
        releases.register(ComponentVersion(info.id, info.version, "domain", info.requires))
        releases.register(ComponentVersion(f"{info.id}.schema", info.schema_version, "schema"))
        releases.register(ComponentVersion(f"{info.id}.policy", info.policy_version, "policy"))
        releases.register(ComponentVersion(f"{info.id}.workflow", info.workflow_version, "workflow"))
    problems = releases.compatibility_check()
    if problems:
        raise RuntimeError(f"Incompatible release: {problems}")
    if invoke_mode == 'local':
        from .adk_agents import ADKLocalInvoker
        invoker = ADKLocalInvoker(registry)
    else:
        invoker = (A2AInvoker if invoke_mode == 'a2a' else RemoteInvoker)(config.WORKSHOP_URL)
    runtime = ZARRuntime(invoker=invoker, store=services.store, assets=services.assets,
                         releases=releases, catalog=catalog)
    return App(registry, catalog, releases, runtime)
