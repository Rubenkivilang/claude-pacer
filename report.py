"""Turn usage + pacing into the three outputs: the tool's JSON, the one human line, the cron gate line."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from .pacing import (DEFAULT_PRIORITY, DEFAULTS, PRIORITIES, Decision, Thresholds, decide, job_runs, number,
                      limit_spent, reserve_status, week_pace)
from .usage import Usage

GUIDANCE = {
    "skip": ("Over budget: do only what was explicitly asked, keep it short, and defer optional or "
             "background work until the windows reset."),
    "light": ("Running hot: keep turns short, avoid subagents and long tool loops, and finish or park "
              "big tasks."),
    "normal": "On pace: work normally.",
    "heavy": ("Spare capacity that would otherwise go unused: a good time for big jobs (subagents, long "
              "research, refactors, backlog)."),
}
# What a vital cron job is told when it runs in skip mode (every other gated job waits). Pacing never stops a
# vital job, so it may be spending the user's reserve: the guidance keeps it to the essential part.
VITAL_SKIP_GUIDANCE = ("Over budget, but this job is vital, so it runs even into the user's reserve: do only its "
                       "essential part, keep turns short, and skip subagents, long tool loops and optional extras.")
EMOJI = {"skip": "🔴", "light": "🟡", "normal": "🟢", "heavy": "🔵"}
RULE_LABELS = {
    "h5_hard_stop": "5h hard stop reached",
    "week_hard_stop": "reserve reached",
    "far_over_pace": "far over pace",
    "h5_hot": "5h window running hot",
    "over_pace": "over pace",
    "no_pace_light": "running hot (weekly reset time unknown)",
    "no_pace": "weekly reset time unknown, no pacing",
    "reset_soon_spare": "spare capacity, week resets soon",
    "under_pace": "well under pace",
    "on_pace": "on pace",
    "usage_unknown": "usage unknown, fail open",
    "error": "pacing error, fail open",
}
BAR_WIDTH = 20  # cells of 5 % each


def hours_until(when: Optional[datetime], now: datetime, ndigits: int = 1) -> Optional[float]:
    return None if when is None else round((when - now).total_seconds() / 3600, ndigits)


def cron_jobs(mode: str, h5: Optional[float], week: Optional[float], t: Thresholds = DEFAULTS) -> dict:
    """What a gated cron job of each priority does right now: "runs", "runs lightly" (vital in skip mode)
    or "waits" (vital only while a limit is used up)."""
    out = {}
    for priority in PRIORITIES:
        if not job_runs(priority, mode, h5, week, t):
            out[priority] = "waits"
        else:
            out[priority] = "runs lightly" if mode == "skip" else "runs"
    return out


def _iso(when: Optional[datetime]) -> Optional[str]:
    return when.isoformat() if when else None


def assess(usage: Usage, now: datetime, t: Thresholds = DEFAULTS, warnings: Optional[list] = None) -> dict:
    """The ``claude_pace`` tool result: mode, reason, guidance, both windows, pace %, hours to reset, and
    the user's reserve (the share of the week agents leave alone) with how it stands."""
    h5w, wkw = usage.five_hour, usage.week
    h5 = h5w.used_pct if h5w else None
    week = wkw.used_pct if wkw else None
    h5_reset = h5w.resets_at if h5w else None
    week_reset = wkw.resets_at if wkw else None
    try:
        d = decide(h5, week, week_reset, now, h5_reset=h5_reset, t=t)
    except Exception as exc:  # defensive: decide() raises only on non-numbers, which usage.py filters out
        d = Decision("normal", "error", f"budget error ({type(exc).__name__}), fail open")
    week_in_h = d.week_resets_in_h if d.week_resets_in_h is not None else hours_until(week_reset, now)
    pace = d.week_pace_pct if d.week_pace_pct is not None else (
        week_pace(week_in_h, t) if week_in_h is not None else None)
    try:
        reserve = reserve_status(week, pace, t)
    except TypeError:  # a non-number that decide() already turned into the error rule
        reserve = None
    out: dict[str, Any] = {
        "mode": d.mode,
        "reason": d.reason,
        "guidance": GUIDANCE[d.mode],
        "five_hour": {"used_pct": h5, "resets_at": _iso(h5_reset), "resets_in_h": hours_until(h5_reset, now, 2),
                      "counted_fresh": d.h5_counted_fresh},
        "week": {"used_pct": week, "resets_at": _iso(week_reset), "resets_in_h": week_in_h, "pace_pct": pace,
                 "reserve_pct": t.reserve_pct, "reserve_status": reserve},
        "rule": d.rule,
        "cron_jobs": cron_jobs(d.mode, number(h5), number(week), t),
    }
    extra = [{"label": w.label, "used_pct": w.used_pct, "resets_at": _iso(w.resets_at)} for w in usage.others]
    if extra:
        out["other_windows"] = extra
    if usage.error:
        out["usage_error"] = usage.error
    if usage.what_if:
        out["what_if"] = True
    out["source"] = f"Hermes account usage ({usage.source})" if usage.source else "Hermes account usage"
    if usage.fetched_at:
        out["fetched_at"] = usage.fetched_at.isoformat()
    if warnings:
        out["config_warnings"] = list(warnings)
    out["summary"] = human_line(out)
    return out


def bar(used: Optional[float], pace: Optional[float], width: int = BAR_WIDTH) -> str:
    """Weekly use vs pace, one cell per 100/width %: █ used within pace, ▓ used beyond pace,
    ▒ still allowed by now (pace not reached yet), ░ the rest of the week."""
    if used is None:
        return ""
    pace = 100.0 if pace is None else pace
    step = 100.0 / width
    cells = []
    for i in range(width):
        mid = (i + 0.5) * step
        if mid < min(used, pace):
            cells.append("█")
        elif mid < used:
            cells.append("▓")
        elif mid < pace:
            cells.append("▒")
        else:
            cells.append("░")
    return "".join(cells)


def fmt_hours(hours: Optional[float]) -> str:
    """6.1 -> '6h 6m', 158.6 -> '6d 15h', 0.3 -> '18 min'; past resets read 'now'."""
    if hours is None:
        return "?"
    minutes = int(round(hours * 60))
    if minutes <= 0:
        return "now"
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        h, m = divmod(minutes, 60)
        return f"{h}h {m}m" if m else f"{h}h"
    d, h = divmod(int(round(hours)), 24)
    return f"{d}d {h}h" if h else f"{d}d"


def _p(value: Optional[float]) -> str:
    value = number(value)
    return "?" if value is None else f"{value:g}%"


def crons_text(jobs: Any) -> str:
    """Which gated cron priorities run right now, for the human line: 'crons run: vital, normal', 'crons run:
    all' / 'none', and 'crons run: vital only, lightly' in skip mode. '' without a ``cron_jobs`` dict."""
    if not isinstance(jobs, dict):
        return ""
    running = [p for p in PRIORITIES if str(jobs.get(p, "")).startswith("runs")]
    if not running:
        return "crons run: none"
    if len(running) == len(PRIORITIES):
        return "crons run: all"
    if any(jobs.get(p) == "runs lightly" for p in running):
        return f"crons run: {', '.join(running)} only, lightly"
    return "crons run: " + ", ".join(running)


def human_line(payload: dict) -> str:
    """One line for /pace and `hermes pace`, e.g.
    🟢 normal · on pace · week 13% ███░░░░░░░░░░░░░░░░░ pace 14.5% · reserve 20%: on track · resets in 6d 15h · 5h 3% (resets in 4h 54m) · crons run: vital, normal, optional"""
    mode = payload.get("mode", "normal")
    head = f"{EMOJI.get(mode, '')} {mode} · {RULE_LABELS.get(str(payload.get('rule')), payload.get('reason', ''))}"
    if payload.get("what_if"):
        head += " (what-if)"
    crons = crons_text(payload.get("cron_jobs"))
    wk, h5 = payload.get("week") or {}, payload.get("five_hour") or {}
    week_used, week_pace_pct, week_in_h = number(wk.get("used_pct")), number(wk.get("pace_pct")), number(wk.get("resets_in_h"))
    h5_used, h5_in_h = number(h5.get("used_pct")), number(h5.get("resets_in_h"))
    if week_used is None and h5_used is None:
        err = " ".join(str(payload.get("usage_error") or "").split())
        if len(err) > 120:
            err = err[:120].rsplit(" ", 1)[0] + " …"
        line = f"{head}: {err}" if err else head
        return f"{line} · {crons}" if crons else line
    parts = [head]
    if week_used is not None:
        week = f"week {_p(week_used)}"
        if week_pace_pct is not None:
            week += f" {bar(week_used, week_pace_pct)} pace {_p(week_pace_pct)}"
        parts.append(week)
        reserve = number(wk.get("reserve_pct"))
        if reserve is not None:
            status = wk.get("reserve_status")
            parts.append(f"reserve {_p(reserve)}" + (f": {status}" if status else ""))
    if week_in_h is not None:
        parts.append(f"resets in {fmt_hours(week_in_h)}")
    if h5_used is not None:
        five = f"5h {_p(h5_used)}"
        if h5_in_h is not None:
            five += f" (resets in {fmt_hours(h5_in_h)}{', counted fresh' if h5.get('counted_fresh') else ''})"
        parts.append(five)
    if crons:
        parts.append(crons)
    return " · ".join(parts)


def gate_why(priority: str, mode: str, payload: dict, runs: bool, h5: Optional[float], week: Optional[float],
             t: Thresholds = DEFAULTS) -> str:
    """One line saying which priority the job has and why it runs or waits, e.g. 'normal job, skip mode (far
    over pace): waits; only vital jobs run in skip mode'."""
    label = RULE_LABELS.get(str(payload.get("rule")), str(payload.get("reason") or mode))
    if payload.get("what_if"):
        label += ", what-if"
    head = f"{priority} job, {mode} mode ({label})"
    if priority == "vital" and not runs:
        return f"{head}: waits, {limit_spent(h5, week)}; vital jobs run again once it resets"
    if mode == "skip":
        if runs:
            return f"{head}: runs lightly; vital jobs may use the reserve, only a used-up limit stops them"
        return f"{head}: waits; only vital jobs run in skip mode"
    if runs:
        return f"{head}: runs"
    if priority == "spare":
        return f"{head}: waits; spare jobs run only in heavy mode"
    return f"{head}: waits; optional jobs run again once the week is back on pace"


def gate_result(payload: dict, priority: str = DEFAULT_PRIORITY, t: Thresholds = DEFAULTS) -> dict:
    """Cron pre-run gate document for a job of ``priority``: ``{"wakeAgent": false, "priority", "why"}`` when the
    job should wait, else ``{"wakeAgent": true, "priority", "why", "context": {...}}`` for the prompt. A vital
    job that runs in skip mode gets ``VITAL_SKIP_GUIDANCE``. Hermes reads only ``wakeAgent``; the rest is for
    the person (or agent) reading the line."""
    if priority not in PRIORITIES:
        priority = DEFAULT_PRIORITY
    mode = payload.get("mode", "normal")
    wk, h5 = payload.get("week") or {}, payload.get("five_hour") or {}
    h5_used, week_used = number(h5.get("used_pct")), number(wk.get("used_pct"))
    runs = job_runs(priority, mode, h5_used, week_used, t)
    why = gate_why(priority, mode, payload, runs, h5_used, week_used, t)
    if not runs:
        doc: dict[str, Any] = {"wakeAgent": False, "priority": priority, "why": why}
        if payload.get("what_if"):
            doc["what_if"] = True
        return doc
    context = {
        "budget_mode": mode,
        "budget_reason": payload.get("reason"),
        "budget_guidance": VITAL_SKIP_GUIDANCE if mode == "skip" else payload.get("guidance"),
        "five_hour_used_pct": h5.get("used_pct"),
        "week_used_pct": wk.get("used_pct"),
        "week_resets_in_h": wk.get("resets_in_h"),
        "week_pace_pct": wk.get("pace_pct"),
    }
    if payload.get("what_if"):  # `hermes pace --week PCT gate`: say the numbers are made up
        context["what_if"] = True
    return {"wakeAgent": True, "priority": priority, "why": why, "context": context}


def gate_line(payload: dict, priority: str = DEFAULT_PRIORITY, t: Thresholds = DEFAULTS) -> str:
    """The gate document as the single stdout line cron parses. Strict JSON: a NaN or infinity raises
    ValueError (``Pacer.gate`` then fails open) rather than printing a token strict parsers reject."""
    return json.dumps(gate_result(payload, priority, t), ensure_ascii=False, allow_nan=False)


def fail_open_gate(priority: str = DEFAULT_PRIORITY) -> str:
    """The gate's line when it fails internally: counted as usage unknown, i.e. mode normal (fail open), so
    every priority but spare runs."""
    if priority not in PRIORITIES:
        priority = DEFAULT_PRIORITY
    if not job_runs(priority, "normal"):
        return json.dumps({"wakeAgent": False, "priority": priority,
                           "why": f"{priority} job: gate error, counted as normal mode, so it waits"})
    return json.dumps({"wakeAgent": True, "priority": priority, "why": f"{priority} job: gate error, fail open",
                       "context": {"budget_mode": "normal", "budget_reason": "gate error, fail open",
                                   "budget_guidance": GUIDANCE["normal"]}})
