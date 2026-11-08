"""``hermes pace`` argparse tree (the handler is ``Pacer.cli``)."""

from __future__ import annotations

import argparse

from .pacing import DEFAULT_PRIORITY, PRIORITIES, number

GATE_HELP = ('Cron pre-run gate: print one JSON line, {"wakeAgent": false, ...} when a job of this priority '
             'should wait')
GATE_DESCRIPTION = (
    "Print exactly one JSON line for a cron job's pre-run script. A job that should wait gets "
    '{"wakeAgent": false, "priority": P, "why": ...} and cron skips the agent run at no token cost; otherwise '
    '{"wakeAgent": true, "priority": P, "why": ..., "context": {budget_mode, budget_reason, budget_guidance, '
    "five_hour_used_pct, week_used_pct, week_resets_in_h, week_pace_pct}} goes into the job's prompt. "
    "--priority decides when the job waits: vital is never stopped by pacing (it runs in every mode, in skip "
    "lightly, and may use the reserve; it waits only while the 5-hour or weekly limit is used up), normal waits in "
    "skip, optional also waits while the week runs hot "
    "(light), spare runs only on spare capacity (heavy). Fails open: when usage can't be read the mode is normal. "
    "Cron only runs scripts from the profile's own scripts folder ($HERMES_HOME/scripts), so call this from a "
    "two-line shim there (see the README)."
)


def percent(text: str) -> float:
    """argparse type for the what-if flags: a finite number. ``float()`` alone would let nan and inf through,
    and they compare false against every threshold and reach the gate JSON as NaN / Infinity."""
    value = number(text)
    if value is None:
        raise argparse.ArgumentTypeError(f"expected a finite percentage such as 70, got {text!r}")
    return value


def setup_parser(parser) -> None:
    parser.add_argument("--json", action="store_true",
                        help="Print the full result (what the claude_pace tool returns) instead of one line")
    parser.add_argument("--week", type=percent, metavar="PCT",
                        help="What-if: pretend the weekly window is at PCT%% (real reset times are kept)")
    parser.add_argument("--h5", type=percent, metavar="PCT",
                        help="What-if: pretend the 5-hour window is at PCT%%")
    sub = parser.add_subparsers(dest="pace_command", metavar="<command>")
    gate = sub.add_parser("gate", help=GATE_HELP, description=GATE_DESCRIPTION)
    gate.add_argument("--priority", choices=PRIORITIES, default=DEFAULT_PRIORITY,
                      help="How much the job matters. vital: never stopped by pacing, runs in skip mode too (lightly, "
                           "into the reserve if need be) and waits only while a limit is used up; normal (default): "
                           "waits in skip mode; optional: also waits "
                           "while the week runs hot; spare: runs only on spare capacity (use-it-or-lose-it jobs)")
