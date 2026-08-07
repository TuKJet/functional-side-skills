from __future__ import annotations

import re
from typing import Any

from .models import AttendanceEvent, ParsedCell


PUNCH_RE = re.compile(r"\n\((?P<punches>[^)]*)\)\s*$")
EVENT_RE = re.compile(
    r"^(?P<name>{events})"
    r"\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}到"
    r"\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}"
    r"(?: \d+(?:\.\d+)?(?:天|小时|分钟))?$"
)


def parse_cell(raw_value: Any, rules: dict[str, Any]) -> ParsedCell:
    raw = "" if raw_value is None else str(raw_value).strip()
    punch_match = PUNCH_RE.search(raw)
    if punch_match:
        punch_suffix = raw[punch_match.start() :]
        body = raw[: punch_match.start()].strip()
        punches = [part.strip() for part in punch_match.group("punches").split(",")]
        punches = [part for part in punches if part and part != "-"]
    else:
        punch_suffix = ""
        body = raw
        punches = []

    shift_label = None
    shift_id = None
    shift_status = None
    base_state = None
    events: list[AttendanceEvent] = []
    unknown_tokens: list[str] = []

    event_pattern = EVENT_RE.pattern.format(
        events="|".join(re.escape(name) for name in rules["events"])
    )
    event_re = re.compile(event_pattern)

    tokens = [token.strip() for token in re.split(r"[,，]", body) if token.strip()]
    for index, token in enumerate(tokens):
        matched_shift = False
        if index == 0:
            for label, definition in rules["shifts"].items():
                prefix = f"{label}:"
                if token.startswith(prefix):
                    shift_label = label
                    shift_id = definition["id"]
                    shift_status = token[len(prefix) :].strip()
                    matched_shift = True
                    break
        if matched_shift:
            continue

        if token in {"未排班", "未排班并打卡", "休息"}:
            base_state = token
            continue

        event_match = event_re.fullmatch(token)
        if event_match:
            events.append(AttendanceEvent(name=event_match.group("name")))
            continue

        unknown_tokens.append(token)

    return ParsedCell(
        raw=raw,
        body=body,
        punch_suffix=punch_suffix,
        punches=punches,
        shift_label=shift_label,
        shift_id=shift_id,
        shift_status=shift_status,
        base_state=base_state,
        events=events,
        unknown_tokens=unknown_tokens,
    )


def replace_shift_and_status(
    parsed: ParsedCell,
    new_shift_label: str,
    new_status: str,
) -> str:
    tokens = [token.strip() for token in re.split(r"[,，]", parsed.body) if token.strip()]
    tail = tokens[1:] if parsed.shift_label and tokens else tokens
    body = ",".join([f"{new_shift_label}:{new_status}", *tail])
    return f"{body}{parsed.punch_suffix}"


def replace_base_state(parsed: ParsedCell, new_state: str) -> str:
    tokens = [token.strip() for token in re.split(r"[,，]", parsed.body) if token.strip()]
    if tokens and parsed.base_state:
        tokens[0] = new_state
    elif tokens:
        tokens.insert(0, new_state)
    else:
        tokens = [new_state]
    return f"{','.join(tokens)}{parsed.punch_suffix}"
