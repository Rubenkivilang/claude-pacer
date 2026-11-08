"""Pacing math for the Claude Pro/Max usage windows. Pure and stdlib-only: no I/O, no Hermes imports.

The user keeps ``reserve_pct`` of the weekly allowance unused for their own chats; agents and cron
jobs are paced to the rest, the weekly target. The weekly window gets a pace line: a straight line
from the reset to that target at the next reset, plus ``pace_burst_pct`` of head start so an
early-week burst doesn't read as over pace:

    target = 100 - reserve
    pace   = min(target, target * elapsed_fraction_of_week + burst)

The weekly hard stop sits on the same line (``week >= target``), so agents and gated cron jobs stop
before the reserve; only a cron job gated as vital goes on into it (see below). The 5-hour window has
hard stops, a "running hot" line and a quiet ceiling for heavy work.
A 5h window that resets within ``h5_fresh_within_min`` minutes counts as fresh (0 %) for the light
and heavy rules when even ``h5_burn_per_min`` of burn can't reach the 5h hard stop before it resets.

Modes, first match wins (``decide``):

    skip    5h >= h5_skip_pct, week >= target (the reserve is reached), or week > pace + pace_skip_margin
    light   5h >= h5_light_pct or week > pace
    heavy   5h < h5_heavy_max and ((resets within heavy_reset_within_h and
                                    week <= target - heavy_reset_margin) or week < heavy_line)
    normal  everything else

Heavy work is back-loaded: the heavy line climbs exponentially from 0 at the reset to the target at
the next reset, so there is little heavy room early in the week and a lot in its last days:

    heavy_line = target * (exp(heavy_ramp * elapsed) - 1) / (exp(heavy_ramp) - 1)

With the default ``heavy_ramp`` 3 and target 80 that is 14.6 % mid-week, 35.6 % with a quarter of the
week left, 58.2 % with 10 % left and 80 % at the reset. ``heavy_ramp`` 0 restores the straight rule:
``heavy_line = pace - pace_heavy_margin``.

Unknown numbers (None) never trigger a rule: no usage at all is ``normal`` (fail open), and an
unknown weekly reset time means no pacing (light at 5h >= h5_light_pct or week >=
no_pace_week_light_pct, never heavy). Non-numeric input raises; callers fail open.

Gated cron jobs carry a priority (``job_runs``), most to least important:

    vital     runs in every mode, never skipped by pacing, and may use the reserve; in skip it runs
              lightly. Only a used-up limit stops it (5h or week at 100 %: the API refuses anyway)
    normal    runs in light, normal and heavy: waits in skip (the default)
    optional  runs in normal and heavy: also waits while the week runs hot (light)
    spare     runs only in heavy: use-it-or-lose-it work on spare capacity

Ported from the wake gate this plugin grew out of. With the default 20 % reserve (target 80, weekly
hard stop 80, use-it-or-lose-it ceiling 55) the rules, defaults and reason strings are the gate's,
minus two inputs that only mean something on that setup (a Hermes-update-pending pause and a "the
human is chatting right now" light rule).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any, Callable, Optional

WEEK_HOURS = 168  # the weekly window's length; fixed by the plan, not a setting
MODES = ("skip", "light", "normal", "heavy")  # least to most generous


@dataclass(frozen=True)
class Thresholds:
    """Every pacing knob (the plugin's settings). The defaults reproduce the original wake gate: a 20 %
    reserve is its weekly target 80, its weekly hard stop 80 and its use-it-or-lose-it ceiling 55."""

    h5_skip_pct: float = 85             # 5h hard stop: skip at or above
    reserve_pct: float = 20             # share of the week kept unused for the user; agents pace to the rest
    pace_burst_pct: float = 10          # head start on the pace line
    pace_skip_margin: float = 15        # skip when week > pace + this
    h5_fresh_within_min: float = 45     # a 5h window resetting this soon may count as fresh ...
    h5_burn_per_min: float = 0.5        # ... unless this burn per minute reaches the hard stop first
    h5_light_pct: float = 60            # light at or above this 5h use
    no_pace_week_light_pct: float = 60  # weekly reset time unknown: light at or above this week use
    h5_heavy_max: float = 50            # heavy only while the 5h window is under this
    heavy_reset_within_h: float = 24    # use it or lose it: week resets within this many hours ...
    heavy_reset_margin: float = 25      # ... with week at least this many points under the target
    pace_heavy_margin: float = 25       # heavy_ramp 0: heavy when week < pace - this
    heavy_ramp: float = 3               # heavy line steepness: target * expm1(k*elapsed)/expm1(k); 0 = straight

    @property
    def week_target_pct(self) -> float:
        """Where the pace line lands at the weekly reset: everything but the reserve."""
        return 100 - self.reserve_pct

    @property
    def week_skip_pct(self) -> float:
        """Weekly hard stop: the target itself, so agents and crons stop before the reserve (vital jobs go on)."""
        return self.week_target_pct

    @property
    def heavy_reset_week_max(self) -> float:
        """Use-it-or-lose-it ceiling: the last hours before the reset count as spare at or under this."""
        return self.week_target_pct - self.heavy_reset_margin


DEFAULTS = Thresholds()

# Accepted range per setting (inclusive). A value outside it falls back to the default.
RANGES: dict[str, tuple[float, float]] = {
    "h5_skip_pct": (0, 100), "reserve_pct": (0, 100),
    "pace_burst_pct": (0, 100), "pace_skip_margin": (0, 100), "h5_fresh_within_min": (0, 300),
    "h5_burn_per_min": (0, 100), "h5_light_pct": (0, 100), "no_pace_week_light_pct": (0, 100),
    "h5_heavy_max": (0, 100), "heavy_reset_within_h": (0, WEEK_HOURS), "heavy_reset_margin": (0, 100),
    "pace_heavy_margin": (0, 100), "heavy_ramp": (0, 10),
}

# Settings that no longer do anything (0.4.0: vital jobs stop only at a used-up limit). Set, they get a
# config warning instead of silently doing nothing.
RETIRED = {
    "vital_h5_stop_pct": "vital jobs stop only when a limit is used up (100%)",
    "vital_week_stop_pct": "vital jobs stop only when a limit is used up (100%)",
}

# A window at this use is spent: Anthropic refuses requests until it resets. The only stop a vital job has.
LIMIT_PCT = 100.0

# Gated cron job priorities, most to least important, and the least generous mode each still runs in
# (vital also runs in skip, until a limit is used up: see ``job_runs``).
PRIORITIES = ("vital", "normal", "optional", "spare")
DEFAULT_PRIORITY = "normal"
RUNS_FROM = {"vital": "light", "normal": "light", "optional": "normal", "spare": "heavy"}

# What the user's reserve looks like right now (``reserve_status``).
RESERVE_ON_TRACK, RESERVE_AT_RISK, RESERVE_IN_USE = "on track", "at risk", "in use"


@dataclass(frozen=True)
class Decision:
    """What ``decide`` concluded. ``rule`` names the rule that fired (stable ids, for labels and tests)."""

    mode: str
    rule: str
    reason: str
    week_resets_in_h: Optional[float] = None
    week_pace_pct: Optional[float] = None
    h5_counted_fresh: bool = False


def number(value: Any) -> Optional[float]:
    """A finite int/float (or numeric string) as float, else None. ``True``/``False`` are not numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        out = float(value)
    elif isinstance(value, str):
        try:
            out = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return out if math.isfinite(out) else None


def setting(get: Callable[[str, Any], Any], name: str, default: float, lo: float, hi: float,
            warnings: list) -> float:
    """``get(name, None)`` validated as a number in ``lo..hi``; the default (plus a warning) otherwise."""
    try:
        raw = get(name, None)
    except Exception as exc:  # a config read must never break pacing
        warnings.append(f"{name}: could not be read ({type(exc).__name__}), using {default:g}")
        return default
    if raw is None:
        return default
    value = number(raw)
    if value is None or not lo <= value <= hi:
        warnings.append(f"{name}: {raw!r} is not a number in {lo:g}..{hi:g}, using {default:g}")
        return default
    return value


def thresholds_from(get: Callable[[str, Any], Any]) -> tuple[Thresholds, list[str]]:
    """Thresholds from a ``get(key, default)`` reader (the plugin's ``ctx.get_config``) + warnings."""
    warnings: list[str] = []
    values = {f.name: setting(get, f.name, getattr(DEFAULTS, f.name), *RANGES[f.name], warnings)
              for f in fields(Thresholds)}
    for name, why in RETIRED.items():
        try:
            if get(name, None) is not None:
                warnings.append(f"{name}: no longer used, {why}")
        except Exception:  # a config read must never break pacing
            pass
    return replace(DEFAULTS, **values), warnings


def _g(value: float) -> str:
    return f"{value:g}"


def _pct(value: float) -> str:
    return f"{value:g}%"


def week_pace(resets_in_h: float, t: Thresholds = DEFAULTS) -> float:
    """Weekly % that may be used by now, with the reset ``resets_in_h`` hours away (1 decimal)."""
    elapsed = min(1.0, max(0.0, 1 - resets_in_h / WEEK_HOURS))
    return round(float(min(t.week_target_pct, t.week_target_pct * elapsed + t.pace_burst_pct)), 1)


def heavy_line(resets_in_h: float, t: Thresholds = DEFAULTS) -> float:
    """Weekly % under which heavy work may run by now (the ``under_pace`` rule), with the reset ``resets_in_h``
    hours away: an exponential climb from 0 at the reset to the target at the next one (``heavy_ramp`` sets
    how steep), so heavy work is back-loaded into the week's last days. ``heavy_ramp`` 0: the straight rule,
    ``pace - pace_heavy_margin``. 1 decimal."""
    if t.heavy_ramp <= 0:
        return round(week_pace(resets_in_h, t) - t.pace_heavy_margin, 1)
    elapsed = min(1.0, max(0.0, 1 - resets_in_h / WEEK_HOURS))
    return round(float(t.week_target_pct * math.expm1(t.heavy_ramp * elapsed) / math.expm1(t.heavy_ramp)), 1)


def reserve_status(week: Optional[float], pace: Optional[float], t: Thresholds = DEFAULTS) -> Optional[str]:
    """Where the user's reserve stands: "in use" once the week reaches the target (agents and gated crons
    stop there and only vital cron jobs go on, so whatever is used from here on comes out of the reserve),
    "at risk" while the week runs over the pace line, "on track" at or under it. None when the weekly use
    is unknown, or when it is under the target but there is no pace line to compare it with (weekly reset
    time unknown)."""
    if week is None:
        return None
    if week >= t.week_target_pct:
        return RESERVE_IN_USE
    if pace is None:
        return None
    return RESERVE_AT_RISK if week > pace else RESERVE_ON_TRACK


def h5_fresh(h5: Optional[float], h5_reset: Optional[datetime], now: datetime,
             t: Thresholds = DEFAULTS) -> Optional[str]:
    """Reason note when the 5h window counts as fresh (it resets within ``h5_fresh_within_min`` and
    h5 + ``h5_burn_per_min`` per minute left stays under ``h5_skip_pct``), else None."""
    if h5 is None or h5_reset is None:
        return None
    left_min = (h5_reset - now).total_seconds() / 60
    if left_min > t.h5_fresh_within_min or h5 + t.h5_burn_per_min * max(0.0, left_min) >= t.h5_skip_pct:
        return None
    return f"5h {_pct(h5)} resets in {max(0, round(left_min))} min, counted fresh"


def decide(h5: Optional[float], week: Optional[float], week_reset: Optional[datetime], now: datetime,
           h5_reset: Optional[datetime] = None, t: Thresholds = DEFAULTS) -> Decision:
    """The pacing decision for both windows (see the module docstring for the rules)."""
    if h5 is not None and h5 >= t.h5_skip_pct:
        return Decision("skip", "h5_hard_stop", f"5h {_pct(h5)} >= {_pct(t.h5_skip_pct)} hard stop")
    if week is not None and week >= t.week_skip_pct:
        return Decision("skip", "week_hard_stop", f"week {_pct(week)} >= {_pct(t.week_skip_pct)} hard stop")
    fresh = h5_fresh(h5, h5_reset, now, t)
    h5_rule = 0.0 if fresh else h5
    note = f"{fresh}; " if fresh and h5 is not None and h5 >= t.h5_heavy_max else ""
    h5_light = (f"5h {_pct(h5)} >= {_pct(t.h5_light_pct)}"
                if h5 is not None and h5_rule is not None and h5_rule >= t.h5_light_pct else None)
    if h5 is None and week is None:
        return Decision("normal", "usage_unknown", "usage unknown, fail open")
    if week_reset is None:
        week_light = (f"week {_pct(week)} >= {_pct(t.no_pace_week_light_pct)}"
                      if week is not None and week >= t.no_pace_week_light_pct else None)
        why = h5_light or week_light
        if why:
            return Decision("light", "no_pace_light", f"{note}{why}; week reset time unknown",
                            h5_counted_fresh=bool(fresh))
        return Decision("normal", "no_pace", f"{note}week reset time unknown, no pacing",
                        h5_counted_fresh=bool(fresh))

    resets_in_h = round((week_reset - now).total_seconds() / 3600, 1)
    pace = week_pace(resets_in_h, t)
    state = (f"week {_pct(week) if week is not None else 'unknown'} vs pace {_pct(pace)}, "
             f"resets in {resets_in_h:.1f}h")
    if week is not None and week > pace + t.pace_skip_margin:
        return Decision("skip", "far_over_pace", f"over pace + {_g(t.pace_skip_margin)}; {state}",
                        resets_in_h, pace, bool(fresh))
    if h5_light:
        return Decision("light", "h5_hot", f"{note}{h5_light}; {state}", resets_in_h, pace, bool(fresh))
    if week is not None and week > pace:
        return Decision("light", "over_pace", f"{note}over pace; {state}", resets_in_h, pace, bool(fresh))
    if h5_rule is not None and h5_rule < t.h5_heavy_max and week is not None:
        if resets_in_h <= t.heavy_reset_within_h and week <= t.heavy_reset_week_max:
            return Decision("heavy", "reset_soon_spare", f"{note}{state}", resets_in_h, pace, bool(fresh))
        if week < heavy_line(resets_in_h, t):
            return Decision("heavy", "under_pace", f"{note}{state}", resets_in_h, pace, bool(fresh))
    return Decision("normal", "on_pace", f"{note}{state}", resets_in_h, pace, bool(fresh))


def budget(h5: Optional[float], week: Optional[float], week_reset: Optional[datetime], now: datetime,
           h5_reset: Optional[datetime] = None, t: Thresholds = DEFAULTS):
    """``(mode, reason, week_resets_in_h, week_pace_pct)``: the original gate's ``budget()`` shape."""
    d = decide(h5, week, week_reset, now, h5_reset=h5_reset, t=t)
    return d.mode, d.reason, d.week_resets_in_h, d.week_pace_pct


def mode_rank(mode: str) -> int:
    """0 (skip) .. 3 (heavy); an unknown mode ranks as normal."""
    return MODES.index(mode) if mode in MODES else MODES.index("normal")


def limit_spent(h5: Optional[float], week: Optional[float]) -> Optional[str]:
    """Why even a vital job waits: a window is used up (``LIMIT_PCT``, 100 %), so the API would refuse the
    run anyway. None otherwise; unknown numbers never stop it. Not a setting: pacing never stops a vital job."""
    if h5 is not None and h5 >= LIMIT_PCT:
        return f"5h {_pct(h5)}: the 5-hour limit is used up"
    if week is not None and week >= LIMIT_PCT:
        return f"week {_pct(week)}: the weekly limit is used up"
    return None


def job_runs(priority: str, mode: str, h5: Optional[float] = None, week: Optional[float] = None,
             t: Thresholds = DEFAULTS) -> bool:
    """Should a gated cron job of ``priority`` run in ``mode``? A vital job is never stopped by pacing: it
    runs in every mode (in skip lightly, into the reserve if it comes to that) and waits only while a limit
    is used up (``limit_spent``). Every other priority runs from its ``RUNS_FROM`` mode up, so never in
    skip. An unknown priority counts as the default one. No threshold in ``t`` moves a vital job's stop."""
    if priority not in RUNS_FROM:
        priority = DEFAULT_PRIORITY
    if priority == "vital":
        return limit_spent(h5, week) is None
    if mode == "skip":
        return False
    return mode_rank(mode) >= mode_rank(RUNS_FROM[priority])
