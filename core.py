"""The ``Pacer``: one object behind the tool, the slash command and the CLI (and so the cron gate)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Callable, Optional

from .pacing import DEFAULT_PRIORITY, PRIORITIES, setting, thresholds_from
from .report import GUIDANCE, assess, fail_open_gate, gate_line, human_line
from .usage import read_usage, what_if

TOOL_NAME = "claude_pace"
TOOLSET = "claude_pacer"
TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Check how fast this Claude Pro/Max subscription is being used before spending a lot of it. "
        "Returns mode (skip | light | normal | heavy) with a one-line reason and guidance, both usage "
        "windows (5-hour and weekly: used %, reset time, hours to reset), the weekly pace % (how much "
        "of the week may be used by now) and the user's reserve (reserve_pct: the share of the week kept "
        "for their own use, which agents are paced to leave alone; only cron jobs gated as vital may run into it; "
        "reserve_status: on track | at risk | "
        "in use), plus cron_jobs: what a gated cron job of each priority (vital, normal, optional, spare) does "
        "right now. Call it before long or optional work (subagents, big research, "
        "refactors) and at the start of scheduled runs, then follow the guidance. No arguments. The plugin "
        "writes nothing itself; each call runs Hermes' own usage check, which refreshes an expired Claude "
        "Code sign-in first, as /usage would."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}
CACHE_SECONDS = 60.0
SLASH_USAGE = ("/pace: Claude usage pace in one line. /pace json: the full result (what the claude_pace "
               "tool returns). Modes: skip, light, normal, heavy.")


def _no_config(_key: str, default: Any = None) -> Any:
    return default


class Pacer:
    """``get_config(key, default)`` is the plugin's ``ctx.get_config`` (settings under
    ``plugins.entries.claude-pacer.settings``), read on every call so a change applies at once.
    ``fetch`` (tests) replaces Hermes' usage fetch; ``now`` (tests) freezes the clock."""

    def __init__(self, get_config: Optional[Callable[..., Any]] = None,
                 fetch: Optional[Callable[[], Any]] = None, now: Optional[Callable[[], datetime]] = None):
        self._get = get_config or _no_config
        self._fetch = fetch
        self._now = now or (lambda: datetime.now(UTC))

    def payload(self, h5: Optional[float] = None, week: Optional[float] = None) -> dict:
        """The full result. ``h5`` / ``week`` override the used percentages (what-if)."""
        thresholds, warnings = thresholds_from(self._get)
        cache = setting(self._get, "cache_seconds", CACHE_SECONDS, 0, 3600, warnings)
        usage = what_if(read_usage(self._fetch, cache_seconds=cache), h5=h5, week=week)
        return assess(usage, self._now(), thresholds, warnings)

    def _safe_payload(self, **what: Optional[float]) -> dict:
        try:
            return self.payload(**what)
        except Exception as exc:  # last resort: pacing must never break a turn
            reason = f"claude-pacer error ({type(exc).__name__}), fail open"
            return {"mode": "normal", "reason": reason, "guidance": GUIDANCE["normal"], "rule": "error",
                    "summary": f"🟢 normal · {reason}"}

    # --- surfaces -------------------------------------------------------------------------------

    def tool(self, args: Optional[dict] = None, **_: Any) -> str:
        """``claude_pace`` tool handler: always a JSON string, never raises."""
        return json.dumps(self._safe_payload(), ensure_ascii=False)

    def slash(self, raw_args: str = "", **_: Any) -> str:
        """``/pace`` handler: one human line; ``/pace json`` for the full result."""
        arg = (raw_args or "").strip().lower()
        if arg in ("help", "-h", "--help", "?"):
            return SLASH_USAGE
        if arg in ("json", "--json"):
            return json.dumps(self._safe_payload(), ensure_ascii=False, indent=2)
        if arg:
            return f"Unknown argument '{raw_args.strip()}'. {SLASH_USAGE}"
        return self._safe_payload().get("summary") or ""

    def gate(self, priority: str = DEFAULT_PRIORITY, h5: Optional[float] = None,
             week: Optional[float] = None) -> str:
        """Cron pre-run gate for a job of ``priority`` (vital | normal | optional | spare): one JSON line,
        ``{"wakeAgent": false, ...}`` when the job should wait. An internal error counts as usage unknown
        (mode normal): every priority but spare runs."""
        if priority not in PRIORITIES:
            priority = DEFAULT_PRIORITY
        try:
            thresholds, _warnings = thresholds_from(self._get)
            return gate_line(self.payload(h5=h5, week=week), priority, thresholds)
        except Exception:
            return fail_open_gate(priority)

    def cli(self, args: Any) -> int:
        """``hermes pace [--json] [--week PCT] [--h5 PCT] [gate [--priority P]]``. Exit 0 always: the gate
        must never fail a cron run, and fail-open answers are answers. (A bad argument never gets here:
        argparse refuses it first, exit 2.)"""
        h5, week = getattr(args, "h5", None), getattr(args, "week", None)
        if getattr(args, "pace_command", None) == "gate":
            print(self.gate(getattr(args, "priority", None) or DEFAULT_PRIORITY, h5=h5, week=week), flush=True)
            return 0
        payload = self._safe_payload(h5=h5, week=week)
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(payload.get("summary") or human_line(payload))
        return 0
