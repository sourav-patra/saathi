"""The caregiver summary (M12; RQ-97): built from enums, flags and rule ids, never from transcript text.

Elder words reach a caregiver only through an explicit "show raw" action (RQ-56a); a summary line cannot carry them
because `SummarySlots` has no field that could hold them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from saathi.domain import Button, Card, Intent, MediaKind, SummaryFlag, SummarySlots

_RULE_ID = re.compile(r"RD-[A-Z]{3}-\d{1,4}")

_KIND_LINE = {
    MediaKind.TEXT: "a text message",
    MediaKind.AUDIO: "a voice note",
    MediaKind.IMAGE: "a photo",
    MediaKind.VIDEO: "a video",
    MediaKind.DOCUMENT: "a document",
    MediaKind.UNSUPPORTED: "something Saathi cannot read (a sticker, contact or location)",
}
_FLAG_LINE: dict[SummaryFlag, str] = {
    "none": "",
    "asr_failed": "The voice note could not be turned into text, so it was not checked by the radar.",
    "media_lost": "The attachment could not be fetched in time. Ask them to send it again.",
    "consent_missing": "No consent is on record for reading this message, so it was not processed.",
    "unsupported": "Nothing was done with it.",
}


def summary_slots(
    kind: MediaKind,
    rule_ids: Sequence[str],
    intent: str,
    *,
    emergency: bool = False,
    scam: bool = False,
    flag: SummaryFlag = "none",
    transcribed: bool = False,
    audio_attached: bool = False,
) -> SummarySlots:
    """The one-line summary as slots. An intent that is not an `Intent` value is stored as `unknown`; a rule id that
    is not a radar rule id is dropped."""
    try:
        known = Intent(intent).value
    except ValueError:
        known = Intent.UNKNOWN.value
    ids = tuple(sorted({r for r in rule_ids if _RULE_ID.fullmatch(r)}))
    return SummarySlots(
        kind=kind,
        intent=known,
        emergency=emergency,
        scam=scam,
        rule_ids=ids,
        transcribed=transcribed,
        audio_attached=audio_attached,
        flag=flag,
    )


def render_card(slots: SummarySlots) -> Card:
    """The card text for a caregiver. No buttons: a summary asks for nothing (Phase 0 sends no order)."""
    if slots.emergency:
        title = "Possible emergency"
    elif slots.scam:
        title = "Possible scam"
    else:
        title = "New message from the elder"
    lines = [f"They sent {_KIND_LINE[slots.kind]}."]
    if slots.emergency:
        lines.append("Radar: possible emergency. Check on them now.")
    if slots.scam:
        lines.append("Radar: possible scam. Do not share an OTP, PIN or bank detail.")
    if slots.rule_ids:
        lines.append("Rules: " + ", ".join(slots.rule_ids))
    if slots.kind is MediaKind.AUDIO and slots.flag == "none":
        lines.append("Transcribed: " + ("yes" if slots.transcribed else "no"))
    if slots.intent != Intent.UNKNOWN.value:
        lines.append(f"Topic: {slots.intent.replace('_', ' ')}")
    if slots.audio_attached:
        lines.append("The original audio is attached.")
    if _FLAG_LINE[slots.flag]:
        lines.append(_FLAG_LINE[slots.flag])
    buttons: list[Button] = []
    return Card(title=title, lines=lines, buttons=buttons)
