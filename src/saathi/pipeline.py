"""Phase 0 pipeline as a LangGraph graph.

    perceive ──► radar ──┬─(emergency/scam)──► alert ──► END
                         └─(clear)──────────► understand ──► notify ──► END

Phase 0 only *summarises* for the caregiver; nothing is ordered. Later phases
add intent subgraphs, the approval interrupt and the policy-gated commit after
``understand``.

All side effects go through small ports (Protocols) so the graph is fully
testable with fakes (tests/test_pipeline.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, StateGraph

from saathi import replies
from saathi.domain import Understanding
from saathi.guards import pii, radar
from saathi.ids import RunId, derive_id
from saathi.legacy import InboundMessage, MediaKind
from saathi.llm.gateway import Gateway
from saathi.observability import observe
from saathi.perception.asr import ASRProvider
from saathi.understand.router import understand


# --------------------------------------------------------------------------- #
# Ports
# --------------------------------------------------------------------------- #
class MediaFetcher(Protocol):
    async def fetch(self, msg: InboundMessage) -> tuple[bytes, str]: ...


class ElderReplier(Protocol):
    async def reply(self, msg: InboundMessage, text: str) -> None: ...


class CaregiverNotifier(Protocol):
    async def summary(self, msg: InboundMessage, transcript: str, u: Understanding) -> None: ...

    async def alert(self, msg: InboundMessage, transcript: str, kind: str, matches: list[str]) -> None: ...


@dataclass
class Deps:
    gateway: Gateway
    asr: ASRProvider
    media: MediaFetcher
    elder: ElderReplier
    caregiver: CaregiverNotifier
    caregiver_name: str = "your family member"


class State(TypedDict, total=False):
    msg: InboundMessage
    transcript: str            # raw, shown only to the caregiver
    redacted: str              # what models and traces see
    radar: dict[str, Any]
    understanding: Understanding
    errors: list[str]


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #
def build_graph(deps: Deps):
    async def perceive(state: State) -> State:
        msg = state["msg"]
        errors: list[str] = list(state.get("errors", []))
        parts: list[str] = []
        if msg.kind == MediaKind.AUDIO and msg.media_id:
            try:
                audio, mime = await deps.media.fetch(msg)
                parts.append((await deps.asr.transcribe(audio, mime)).text)
            except Exception as e:  # never drop a message because ASR failed
                errors.append(f"asr: {e}")
                parts.append("[voice note — transcription failed]")
        elif msg.kind in (MediaKind.IMAGE, MediaKind.VIDEO, MediaKind.DOCUMENT):
            parts.append(f"[{msg.kind.value} received]")  # vision arrives in Phase 3
        if msg.text:
            parts.append(msg.text)
        transcript = "\n".join(p for p in parts if p).strip()
        return {"transcript": transcript, "redacted": pii.redact(transcript).text, "errors": errors}

    async def scan(state: State) -> State:
        r = radar.scan(state.get("transcript"))
        return {"radar": {"emergency": r.emergency, "scam": r.scam, "matches": r.matches}}

    async def alert(state: State) -> State:
        msg, r = state["msg"], state["radar"]
        kind = "EMERGENCY" if r["emergency"] else "SCAM"
        text = (
            replies.emergency_to_elder(deps.caregiver_name)
            if r["emergency"]
            else replies.scam_to_elder(deps.caregiver_name)
        )
        # Alert the humans first; the reply to the elder is best-effort.
        await deps.caregiver.alert(msg, state.get("transcript", ""), kind, r["matches"])
        try:
            await deps.elder.reply(msg, text)
        except Exception as e:
            return {"errors": [*state.get("errors", []), f"elder reply: {e}"]}
        return {}

    async def classify(state: State) -> State:
        try:
            run = RunId(derive_id("run", state["msg"].id))
            u = await understand(deps.gateway, state.get("redacted") or "(empty message)", run=run)
        except Exception as e:
            u = Understanding(intent="unknown", confidence=0.0, summary_en=f"(router failed: {e})")
        return {"understanding": u}

    async def notify(state: State) -> State:
        msg = state["msg"]
        await deps.caregiver.summary(msg, state.get("transcript", ""), state["understanding"])
        try:
            await deps.elder.reply(msg, replies.ack_to_elder(deps.caregiver_name))
        except Exception as e:
            return {"errors": [*state.get("errors", []), f"elder reply: {e}"]}
        return {}

    def after_radar(state: State) -> str:
        r = state.get("radar") or {}
        return "alert" if (r.get("emergency") or r.get("scam")) else "classify"

    def traced(name: str, as_type: str, fn):
        async def wrapper(state: State) -> State:
            with observe(name, as_type=as_type) as obs:
                out = await fn(state)
                obs.update(output={k: _brief(v) for k, v in (out or {}).items()})
                return out

        return wrapper

    g = StateGraph(State)
    g.add_node("perceive", traced("perceive", "chain", perceive))
    g.add_node("scan", traced("radar", "guardrail", scan))
    g.add_node("alert", traced("alert", "tool", alert))
    g.add_node("classify", traced("understand", "chain", classify))
    g.add_node("notify", traced("notify", "tool", notify))
    g.set_entry_point("perceive")
    g.add_edge("perceive", "scan")
    g.add_conditional_edges("scan", after_radar, {"alert": "alert", "classify": "classify"})
    g.add_edge("alert", END)
    g.add_edge("classify", "notify")
    g.add_edge("notify", END)
    return g.compile()


def _brief(v: Any) -> Any:
    if hasattr(v, "model_dump"):
        return v.model_dump(mode="json")
    return v


async def run_request(graph, msg: InboundMessage) -> State:
    """Run one elder message as one Langfuse trace."""
    with observe(
        "elder_request",
        as_type="agent",
        input={"channel": msg.channel.value, "kind": msg.kind.value},
        metadata={"message_id": msg.id},
    ) as root:
        out = await graph.ainvoke({"msg": msg})
        u = out.get("understanding")
        root.update(
            output={
                "intent": u.intent if u else None,
                "radar": out.get("radar"),
                "errors": out.get("errors"),
            }
        )
        return out
