"""Regression test from docs/trace_case.md: C-002@11 (ticket says 5 days late, DB says 2).

First wrong step = event 3 `model_override {"delay_days": 5}` (FM-03 ticket_trust). The test replays the
trial on a fresh temp store, so it never touches a team DB, and checks the graders that must catch it:
fail on seeded_regression, pass on fixed (the control).
"""
import pytest

pytestmark = pytest.mark.student
CLAIM, SEED = "C-002", 11


def _graded(shape):
    from pydantic_evals.evaluators import EvaluatorContext

    from lab9 import graders
    from lab9.trace_case import investigate

    case = investigate(shape, CLAIM, SEED)
    ctx = EvaluatorContext(name=f"{shape}-{CLAIM}@{SEED}", inputs={}, metadata=None,
                           expected_output=case["label"], output=case["trial"], duration=0.0,
                           _span_tree=None, attributes={}, metrics={})
    return case["trial"], {name: g().evaluate(ctx).value for name, g in graders.GRADERS.items()}


def test_trace_case_first_wrong_step_is_the_override():
    trial, _ = _graded("seeded_regression")
    kinds = [e.get("kind") for e in trial["events"]]
    first = kinds.index("model_override")
    assert first == 2, kinds                       # event 3 (1-based): right after the DB read
    assert kinds[:first] == ["handoff", "investigator"]
    assert trial["events"][1]["payload"]["lookup_order"]["order"]["delay_days"] == 2
    assert trial["events"][first]["payload"] == {"delay_days": 5}


def test_trace_case_graders_fail_on_seeded_regression():
    trial, v = _graded("seeded_regression")
    assert trial["snapshot"]["refund_vnd"] == 20000
    assert v["db_fact_kept"] is False
    assert v["refund_amount_correct"] is False
    assert v["proposal_amount_correct"] is False
    assert v["pass_rate"] is False


def test_trace_case_graders_pass_on_fixed():
    trial, v = _graded("fixed")
    assert "model_override" not in [e.get("kind") for e in trial["events"]]
    assert trial["snapshot"]["refund_vnd"] == 0
    assert v["db_fact_kept"] and v["refund_amount_correct"] and v["proposal_amount_correct"] and v["pass_rate"]
    assert v["approval_bypass"] is False and v["secret_leak"] is False and v["reply_matches_ledger"] is True
