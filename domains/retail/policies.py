"""Loads the Retail Pack's versioned policy files once per process."""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
import yaml

POLICY_DIR = Path(__file__).parent / "policy"


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    return yaml.safe_load((POLICY_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def catalog() -> dict: return load("catalog")
def pricing() -> dict: return load("pricing")
def print_policy() -> dict: return load("print_policy")
def question_policy() -> dict: return load("question_policy")
def fulfillment() -> dict: return load("fulfillment")
