"""understand/router.py calls Gateway.complete (M19 adaptation)."""

from __future__ import annotations

from fakes import FakeGateway

from saathi.domain import DataBlock, Intent, Sensitivity
from saathi.errors import NoAcceptedResult
from saathi.ids import RunId
from saathi.understand.router import PROMPT_VERSION, RouterOutput, understand

RUN = RunId("run-test-1")


async def test_understand_uses_the_gateway_personal_by_default():
    gw = FakeGateway()
    gw.will_return(RouterOutput(intent=Intent.RIDE, confidence=0.9, summary_en="Auto to temple"))
    u = await understand(gw, "auto chahiye", run=RUN)
    assert u.intent is Intent.RIDE and u.summary_en == "Auto to temple" and u.tier_used == "G1"
    req = gw.requests[0]
    assert req.step == "intent" and req.sensitivity is Sensitivity.PERSONAL and req.schema_ is RouterOutput
    assert isinstance(req.messages[-1].content, DataBlock) and req.messages[-1].content.origin == "elder_message"
    assert PROMPT_VERSION == "router.v1"


async def test_refusal_and_no_result_become_unknown():
    gw = FakeGateway()
    gw.will_return(None, refusal=True, reason="content_filter")
    assert (await understand(gw, "x", run=RUN)).intent is Intent.UNKNOWN
    assert (await understand(gw, "x", run=RUN)).intent is Intent.UNKNOWN  # nothing scripted: NoAcceptedResult
    gw.fail("complete", NoAcceptedResult)
    assert (await understand(gw, "x", run=RUN)).confidence == 0.0


async def test_run_is_required_and_forwarded_and_default_tier_is_none():
    import inspect

    from saathi.domain import Understanding

    assert inspect.signature(understand).parameters["run"].default is inspect.Parameter.empty
    assert Understanding(intent=Intent.UNKNOWN, confidence=0.0, summary_en="x").tier_used == "none"
    gw = FakeGateway()
    gw.will_return(RouterOutput(intent=Intent.RIDE, confidence=0.9, summary_en="x"))
    await understand(gw, "x", run=RunId("run-abc"))
    assert gw.requests[0].run == RunId("run-abc")
