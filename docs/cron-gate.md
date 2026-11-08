# Cron gate

`hermes pace gate` is a pre-run script for Hermes cron jobs. Before each run it decides whether the
job should run at all, and if it runs, it hands the job the current mode. A job that waits costs no
tokens: Hermes never starts the agent.

```
hermes pace [--week PCT] [--h5 PCT] gate [--priority vital|normal|optional|spare]
```

## What Hermes does with the line

The gate prints exactly one line of strict JSON and exits 0. Hermes cron then does this (in
`cron/scheduler.py` and `cron/scheduler_prompt.py`):

| Gate output | Hermes cron |
|---|---|
| Last non-empty stdout line is JSON with `"wakeAgent": false` | Skips the agent run: no model call, no delivery. The run is recorded with "Script gate returned `wakeAgent=false` — agent skipped." |
| Anything else, exit 0 (`"wakeAgent": true` here) | Runs the agent, with the script's stdout placed at the top of the prompt |
| Script exits non-zero, or times out | Runs the agent with a "Script Error" block instead, telling it to report the failure |

Hermes reads only `wakeAgent`. The other keys (`priority`, `why`, `context`) are for the agent and for
you.

## A job that waits

```json
{"wakeAgent": false, "priority": "normal", "why": "normal job, skip mode (far over pace): waits; only vital jobs run in skip mode"}
```

`why` always names the priority, the mode, the rule label and the outcome. The other wait reasons:

| `why` ends with | When |
|---|---|
| `waits; only vital jobs run in skip mode` | normal, optional or spare job in skip mode |
| `waits, 5h 100%: the 5-hour limit is used up; vital jobs run again once it resets` | vital job while a limit is used up (the only time a vital job waits) |
| `waits; optional jobs run again once the week is back on pace` | optional job in light mode |
| `waits; spare jobs run only in heavy mode` | spare job in skip, light or normal mode |

## A job that runs: the mode in its prompt

```json
{"wakeAgent": true, "priority": "normal", "why": "normal job, light mode (over pace): runs", "context": {"budget_mode": "light", "budget_reason": "over pace; week 55% vs pace 50%, resets in 84.0h", "budget_guidance": "Running hot: keep turns short, avoid subagents and long tool loops, and finish or park big tasks.", "five_hour_used_pct": 10.0, "week_used_pct": 55.0, "week_resets_in_h": 84.0, "week_pace_pct": 50.0}}
```

Hermes prepends that line to the job's prompt as a fenced block. That part of the prompt reads:

````
## Script Output
The following data was collected by a pre-run script. Use it as context for your analysis.

```
{"wakeAgent": true, "priority": "normal", "why": "normal job, light mode (over pace): runs", "context": {"budget_mode": "light", ...}}
```

Work the backlog. Size the work to budget_mode.
````

| `context` key | Content |
|---|---|
| `budget_mode` | skip, light, normal or heavy: the real mode, also for a vital job in skip |
| `budget_reason` | The reason string, e.g. `over pace; week 55% vs pace 50%, resets in 84.0h` |
| `budget_guidance` | The mode's guidance sentence; a vital job in skip gets the vital text ([Job priorities](job-priorities.md#vital-jobs-in-skip-mode)) |
| `five_hour_used_pct`, `week_used_pct` | Used percentages, `null` when unknown |
| `week_resets_in_h` | Hours to the weekly reset, `null` when unknown |
| `week_pace_pct` | The pace line right now, `null` without a reset time |
| `what_if` | `true` only when `--week` / `--h5` were given |

The gate doesn't change the job's prompt text; it only adds data. Make the prompt use it. "Size the
work to budget_mode." is enough for most jobs; for more control spell it out, for example: "If
budget_mode is light, only triage new items. If heavy, also clear the oldest five."

## Shims

Cron runs pre-run scripts only from `$HERMES_HOME/scripts` of the job's profile, and `script` takes
no arguments. So each priority gets a two-line shim:

```bash
scripts="$(dirname "$(hermes config path)")/scripts"
mkdir -p "$scripts"
printf '#!/usr/bin/env bash\nexec hermes pace gate "$@"\n' > "$scripts/claude-pace-gate.sh"
printf '#!/usr/bin/env bash\nexec hermes pace gate --priority vital "$@"\n' > "$scripts/claude-pace-gate-vital.sh"
hermes cron create "0 */2 * * *" "Work the backlog. Size the work to budget_mode." \
  --name backlog --script claude-pace-gate.sh
```

For a named profile add `-p <name>` to `hermes config path` and `hermes cron create`. The shim itself
needs no profile flag: cron runs it with that profile's `HERMES_HOME`, and the plugin reads that
profile's settings.

## Testing a gate

`--week` and `--h5` replace the used percentages for one check; the reset times stay real. They go
before `gate`:

```bash
hermes pace --week 90 gate                      # {"wakeAgent": false, ...}
hermes pace --week 90 gate --priority vital     # {"wakeAgent": true, ...}, runs lightly
hermes pace --h5 96 gate --priority vital       # {"wakeAgent": true, ...}, pacing never stops vital
hermes pace --h5 100 gate --priority vital      # {"wakeAgent": false, ...}, the 5-hour limit is used up
```

A what-if answer carries `"what_if": true` and the rule label says `what-if`. Don't put `--week` or
`--h5` in a real shim.

## Failure behaviour

| Situation | Gate output |
|---|---|
| Usage can't be read | Mode normal (fail open): vital, normal and optional jobs run, spare jobs wait. `budget_reason` is `usage unknown, fail open` |
| Internal error in the gate | Counted as normal mode: `{"wakeAgent": true, "priority": "normal", "why": "normal job: gate error, fail open", ...}`; a spare job gets `"wakeAgent": false` with `spare job: gate error, counted as normal mode, so it waits` |
| Bad argument (`--priority urgent`, `--week nan`) | argparse error, exit 2, so cron runs the agent with a Script Error block. Run a new shim by hand once |
| `hermes` not on cron's `PATH` | The shim's `exec` fails, exit non-zero: same as above |

Given valid arguments the gate always exits 0. Fail open is a deliberate choice for a cost gate: a
broken usage read should not silence every scheduled job. There is no fail-closed switch. The only
priority that waits when usage is unknown is `spare`, which also waits in every mode but heavy.

Each gate run is a separate process, so the 60-second usage cache does not apply: every run reads
usage once through Hermes. See [Security and privacy](security-and-privacy.md#network-calls).
