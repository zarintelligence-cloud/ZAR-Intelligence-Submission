"""Loads Domain Packs by manifest. Adding a pack never requires editing Core."""
from __future__ import annotations
import importlib
from pathlib import Path
from typing import Any
import yaml
from .contracts import DomainInfo
from .registry import CapabilityRegistry
from .workflow import WorkflowEngine, WorkflowSpec


class LoadedPack:
    def __init__(self, info: DomainInfo, root: Path, module: Any, workflow: WorkflowEngine | None):
        self.info, self.root, self.module, self.workflow = info, root, module, workflow


class DomainCatalog:
    def __init__(self) -> None:
        self.packs: dict[str, LoadedPack] = {}

    def load(self, pack_dir: str | Path, registry: CapabilityRegistry | None = None) -> LoadedPack:
        root = Path(pack_dir)
        raw = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
        info = DomainInfo(**raw["domain"])
        if info.lifecycle == "retired":
            raise ValueError(f"Domain pack {info.id} is retired")
        module = importlib.import_module(info.module)
        if registry is not None:                     # capability host (workshop / monolith)
            module.register(registry, info)
        workflow = None
        wf_path = root / "workflow.yaml"
        if wf_path.exists():                         # orchestrator (Polaris / monolith)
            spec = WorkflowSpec(**yaml.safe_load(wf_path.read_text(encoding="utf-8"))["workflow"])
            workflow = WorkflowEngine(spec, getattr(module, "WORKFLOW_HANDLERS", {}))
        pack = LoadedPack(info, root, module, workflow)
        self.packs[info.id] = pack
        return pack

    def get(self, domain_id: str) -> LoadedPack:
        if domain_id not in self.packs:
            raise KeyError(f"Domain not loaded: {domain_id}")
        return self.packs[domain_id]
