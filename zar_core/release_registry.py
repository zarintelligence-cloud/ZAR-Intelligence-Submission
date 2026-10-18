from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ComponentVersion:
    name: str
    version: str
    kind: str
    requires: dict[str, str] = field(default_factory=dict)   # dependency -> required MAJOR


class ReleaseRegistry:
    def __init__(self, release_id: str = "") -> None:
        self.release_id = release_id
        self.items: dict[str, ComponentVersion] = {}

    def register(self, item: ComponentVersion) -> None:
        self.items[item.name] = item

    def manifest(self) -> dict[str, str]:
        out = {name: item.version for name, item in sorted(self.items.items())}
        if self.release_id:
            out["release_id"] = self.release_id
        return out

    def compatibility_check(self) -> list[str]:
        problems = []
        for item in self.items.values():
            for dep, major in item.requires.items():
                found = self.items.get(dep)
                if not found or found.version.split(".")[0] != str(major):
                    problems.append(f"{item.name} requires {dep} major {major}")
        return problems
