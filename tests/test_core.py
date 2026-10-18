import pytest
from pydantic import ValidationError
from zar_core.contracts import CapabilityRequest, CapabilityResult, Context
from zar_core.decision.bayes import BayesianNetwork, BinaryNode
from zar_core.decision.conformal import SplitConformalRegressor
from zar_core.decision.monte_carlo import MonteCarlo
from zar_core.decision.optimization import ConstraintOptimizer
from zar_core.decision.voi import rank_questions, select_questions
from zar_core.policy import DecisionTable, PolicyEngine
from zar_core.registry import CapabilityRegistry
from zar_core.release_registry import ComponentVersion, ReleaseRegistry

CTX = Context(tenant_id="t", domain="d", session_id="s")


def test_contract_rejects_malformed_envelope():
    with pytest.raises(ValidationError):
        CapabilityRequest(request_id="1", capability="x", payload={})          # no context
    with pytest.raises(ValidationError):
        CapabilityResult(request_id="1", capability="x", status="maybe")


def test_registry_routes_by_domain_with_wildcard_and_errors():
    r = CapabilityRegistry()
    r.register("d", "a", lambda q: CapabilityResult(request_id=q.request_id, capability="a", status="ok"))
    r.register("*", "b", lambda q: CapabilityResult(request_id=q.request_id, capability="b", status="ok", data={"w": 1}))
    with pytest.raises(ValueError):
        r.register("d", "a", lambda q: None)
    assert r.invoke(CapabilityRequest(request_id="1", context=CTX, capability="a")).status == "ok"
    assert r.invoke(CapabilityRequest(request_id="2", context=CTX, capability="b")).data == {"w": 1}
    assert r.invoke(CapabilityRequest(request_id="3", context=CTX, capability="zzz")).status == "error"

    def boom(q): raise RuntimeError("secret internals")
    r.register("d", "c", boom)
    res = r.invoke(CapabilityRequest(request_id="4", context=CTX, capability="c"))
    assert res.status == "error" and "RuntimeError" in res.error


def test_release_compatibility():
    rr = ReleaseRegistry("r1")
    rr.register(ComponentVersion("zar-core", "3.0.0", "core"))
    rr.register(ComponentVersion("pack", "1.0.0", "domain", {"zar-core": "3"}))
    assert rr.compatibility_check() == [] and rr.manifest()["release_id"] == "r1"
    rr.register(ComponentVersion("old", "1.0.0", "domain", {"zar-core": "2"}))
    assert rr.compatibility_check() == ["old requires zar-core major 2"]


def test_policy_engine_and_decision_table():
    res = PolicyEngine().evaluate({"budget": 90000}, [{"field": "budget", "op": "lte", "value": 80000,
                                                       "message": "Budget exceeded"}])
    assert not res.allowed and res.reasons == ["Budget exceeded"]
    t = DecisionTable([{"id": "R1", "when": [{"field": "x", "op": "gt", "value": 5}], "then": "big"}], "small")
    assert t.decide({"x": 9}).action == "big" and t.decide({"x": 1}).rule_id == "default"


def test_decision_plugins():
    bn = BayesianNetwork([BinaryNode("risk", [], {(): 0.2}),
                          BinaryNode("late", ["risk"], {(False,): 0.1, (True,): 0.8})])
    assert abs(bn.query("late", {}) - 0.24) < 1e-9 and bn.query("late", {"risk": True}) == pytest.approx(0.8)
    assert bn.query("risk", {"late": True}) > 0.2                          # diagnostic update
    best = ConstraintOptimizer().choose([{"cost": 100, "days": 4}, {"cost": 80, "days": 9}],
                                        [lambda x: x["days"] <= 5], lambda x: x["cost"])
    assert best["cost"] == 100
    mc = MonteCarlo().run(lambda r: r.uniform(4, 10), lambda d: d <= 8, n=2000)
    assert 0.55 < mc["success_rate"] < 0.78 and mc["p50"] < mc["p90"]
    assert MonteCarlo().run(lambda r: r.random(), lambda v: v < .5, seed=7) == \
        MonteCarlo().run(lambda r: r.random(), lambda v: v < .5, seed=7)    # reproducible
    lo, hi = SplitConformalRegressor().fit([5, 7, 8], [5.5, 6.5, 8.2], coverage=.8).interval(7)
    assert lo < 7 < hi
    assert rank_questions(0.5, {"q1": (0.9, 0.1), "q2": (0.6, 0.4)})[0][0] == "q1"


def test_select_questions_essentials_first_and_capped():
    fields = {"a": {"essential": True, "question": "A?"},
              "b": {"essential": False, "p_default_wrong": 0.5, "cost_if_wrong": 1, "question": "B?"},
              "c": {"essential": False, "p_default_wrong": 0.1, "cost_if_wrong": 1, "question": "C?"}}
    qs = select_questions(["c", "b", "a"], fields, max_questions=3, threshold=0.15)
    assert [q["field"] for q in qs] == ["a", "b"]
