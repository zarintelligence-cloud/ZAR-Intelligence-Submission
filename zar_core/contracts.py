"""Stable ZAR Core contracts (contract schema 3.0.0). No domain nouns here."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Context(BaseModel):
    tenant_id: str
    domain: str
    session_id: str
    user_id: str | None = None
    locale: str = "en"
    metadata: dict[str, Any] = Field(default_factory=dict)


class Goal(BaseModel):
    name: str
    description: str = ""
    success_criteria: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    key: str
    value: Any
    source: str
    confidence: float = 1.0
    observed_at: str = Field(default_factory=utcnow)


Status = Literal["ok", "needs_input", "error"]


class CapabilityRequest(BaseModel):
    request_id: str
    context: Context
    capability: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CapabilityResult(BaseModel):
    request_id: str
    capability: str
    status: Status
    data: dict[str, Any] = Field(default_factory=dict)
    questions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float | None = None
    model_used: str = ""
    error: str | None = None
    debug: dict[str, Any] = Field(default_factory=dict)   # 3.1.0: prompts, raw model output, rule traces


class DecisionRequest(BaseModel):
    context: Context
    goal: Goal
    evidence: list[Evidence] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    candidate_actions: list[dict[str, Any]] = Field(default_factory=list)


class Decision(BaseModel):
    decision_id: str
    session_id: str
    goal: str
    action: str
    engine: str
    confidence: float | None = None
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    options: list[dict[str, Any]] = Field(default_factory=list)
    release_manifest: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utcnow)


class DomainInfo(BaseModel):
    id: str
    name: str
    version: str
    schema_version: str
    policy_version: str
    workflow_version: str = "0.0.0"
    module: str
    requires: dict[str, str] = Field(default_factory=dict)
    lifecycle: Literal["development", "experimental", "preview", "stable",
                       "deprecated", "retired"] = "development"
    capabilities: dict[str, str] = Field(default_factory=dict)   # capability -> provider
    objectives: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    release_notes: list[str] = Field(default_factory=list)


class SessionEvent(BaseModel):
    event: str
    data: dict[str, Any] = Field(default_factory=dict)
