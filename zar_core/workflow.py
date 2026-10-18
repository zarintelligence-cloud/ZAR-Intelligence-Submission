"""Generic, declarative state machine. States/events are data supplied by a Domain Pack."""
from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from pydantic import BaseModel, Field


class Transition(BaseModel):
    source: str = Field(alias="from")
    event: str
    handler: str


class WorkflowSpec(BaseModel):
    id: str
    version: str
    initial: str
    terminal: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    transitions: list[Transition]


@dataclass
class StepResult:
    state: str | None = None                  # None = stay in current state
    patch: dict[str, Any] = field(default_factory=dict)
    message: str | None = None                # customer-facing text (templated)
    emit: str | None = None                   # internal follow-up event (auto-chaining)
    emit_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepContext:
    session: dict[str, Any]
    event: str
    data: dict[str, Any]
    runtime: Any                               # ZARRuntime (typed loosely to avoid a cycle)


Handler = Callable[[StepContext], StepResult]


class InvalidTransition(ValueError):
    pass


class WorkflowEngine:
    def __init__(self, spec: WorkflowSpec, handlers: dict[str, Handler]) -> None:
        self.spec, self.handlers = spec, handlers
        if spec.initial not in spec.states or not set(spec.terminal) <= set(spec.states):
            raise ValueError("Initial and terminal states must be declared")
        keys = [(t.source, t.event) for t in spec.transitions]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate workflow transitions")
        missing = {t.handler for t in spec.transitions} - set(handlers)
        if missing:
            raise ValueError(f"Workflow {spec.id} references unknown handlers: {sorted(missing)}")
        if spec.states:
            unknown = {t.source for t in spec.transitions if t.source != "*"} - set(spec.states)
            if unknown:
                raise ValueError(f"Transitions from undeclared states: {sorted(unknown)}")

    def find(self, state: str, event: str) -> Transition | None:
        exact = [t for t in self.spec.transitions if t.source == state and t.event == event]
        wild = [t for t in self.spec.transitions if t.source == "*" and t.event == event]
        return (exact or wild or [None])[0]

    def allowed_events(self, state: str) -> list[str]:
        if state in self.spec.terminal:
            return sorted({t.event for t in self.spec.transitions if t.source == state})
        return sorted({t.event for t in self.spec.transitions if t.source in (state, "*")})

    def step(self, session: dict[str, Any], event: str, data: dict[str, Any], runtime: Any) -> StepResult:
        state = session["state"]
        if state in self.spec.terminal and not any(t.source == state and t.event == event for t in self.spec.transitions):
            raise InvalidTransition(f"Session is in terminal state '{state}'")
        t = self.find(state, event)
        if t is None:
            raise InvalidTransition(f"Event '{event}' is not allowed in state '{state}'. "
                                    f"Allowed: {self.allowed_events(state)}")
        result = self.handlers[t.handler](StepContext(session, event, data, runtime))
        if result.state and result.state not in self.spec.states:
            raise ValueError("Handler returned an undeclared state")
        return result
