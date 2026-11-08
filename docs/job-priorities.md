# Job priorities

Not every cron job can wait. A gated job carries a priority, and the priority decides in which modes
it runs. There are four, most to least important. The default is `normal`.

| Priority | Runs in | Waits in | Use it for |
|---|---|---|---|
| `vital` | every mode, in skip lightly; pacing never stops it, and it may use the reserve | only while a limit is used up: 5h or week at 100 % | jobs your setup can't do without: order or inbox watchers, alerts, monitors |
| `normal` | light, normal, heavy | skip | regular work |
| `optional` | normal, heavy | skip, light | nice-to-have jobs: digests, cleanups, low-priority research |
| `spare` | heavy | skip, light, normal | use-it-or-lose-it work: the big backlog, refactors, long research |

The rule is `job_runs()` in `pacing.py`: a vital job runs in every mode unless a limit is used up
(`limit_spent()`); every other job never runs in skip, and in the other modes runs when the mode is at
least its starting mode (normal from light, optional from normal, spare from heavy).

## Setting a priority

The priority is a flag on the gate: `hermes pace gate --priority vital|normal|optional|spare`. A cron
job's `script` takes no arguments, so put the flag in the shim, one shim per priority:

```bash
scripts="$(dirname "$(hermes config path)")/scripts"
mkdir -p "$scripts"
printf '#!/usr/bin/env bash\nexec hermes pace gate "$@"\n' > "$scripts/claude-pace-gate.sh"
printf '#!/usr/bin/env bash\nexec hermes pace gate --priority vital "$@"\n' > "$scripts/claude-pace-gate-vital.sh"
printf '#!/usr/bin/env bash\nexec hermes pace gate --priority optional "$@"\n' > "$scripts/claude-pace-gate-optional.sh"
printf '#!/usr/bin/env bash\nexec hermes pace gate --priority spare "$@"\n' > "$scripts/claude-pace-gate-spare.sh"
```

Then point each job at the shim for its priority:

```bash
hermes cron create "*/30 * * * *" "Check the shop's order inbox. Size the work to budget_mode." \
  --name orders --script claude-pace-gate-vital.sh
hermes cron create "0 */2 * * *" "Work the backlog. Size the work to budget_mode." \
  --name backlog --script claude-pace-gate.sh
hermes cron create "0 7 * * *" "Write the morning digest. Size the work to budget_mode." \
  --name digest --script claude-pace-gate-optional.sh
hermes cron create "0 */3 * * *" "Take the next refactor from the list. Size the work to budget_mode." \
  --name refactor --script claude-pace-gate-spare.sh
```

There is no priority in `config.yaml`: it lives only in which shim a job uses. To change a job's
priority, point it at another shim (`hermes cron edit`). An invalid value such as
`--priority urgent` is refused by the argument parser with exit code 2, so a typo shows up the first
time you run the shim by hand.

## Vital jobs in skip mode

Pacing never skips a vital job. In skip mode it still runs, with two differences:

- `budget_mode` in its prompt stays `"skip"`, the real mode.
- `budget_guidance` is the vital text instead of the skip text: "Over budget, but this job is vital,
  so it runs even into the user's reserve: do only its essential part, keep turns short, and skip
  subagents, long tool loops and optional extras."

In light mode a vital job gets light's guidance, like every job that runs there.

It waits only when a limit is really used up: the 5-hour window or the week at 100 %, where Anthropic
refuses the run anyway. That stop is not a setting (0.3.0's `vital_h5_stop_pct` and
`vital_week_stop_pct` are no longer used). Because the week may be past the target, vital jobs can use
part of your [reserve](reserve.md#vital-jobs-and-the-reserve), up to all of it.

Week at 90 % (what-if), defaults (fixture: 5h at 42 %, 111.1 h to the reset):

```
$ hermes pace --week 90 gate
{"wakeAgent": false, "priority": "normal", "why": "normal job, skip mode (reserve reached, what-if): waits; only vital jobs run in skip mode", "what_if": true}

$ hermes pace --week 90 gate --priority vital
{"wakeAgent": true, "priority": "vital", "why": "vital job, skip mode (reserve reached, what-if): runs lightly; vital jobs may use the reserve, only a used-up limit stops them", "context": {"budget_mode": "skip", "budget_reason": "week 90% >= 80% hard stop", "budget_guidance": "Over budget, but this job is vital, so it runs even into the user's reserve: do only its essential part, keep turns short, and skip subagents, long tool loops and optional extras.", "five_hour_used_pct": 42.0, "week_used_pct": 90.0, "week_resets_in_h": 111.1, "week_pace_pct": 37.1, "what_if": true}}
```

Only with the 5-hour window used up (100 %) does the vital job wait:

```json
{"wakeAgent": false, "priority": "vital", "why": "vital job, skip mode (5h hard stop reached): waits, 5h 100%: the 5-hour limit is used up; vital jobs run again once it resets"}
```

## What runs right now

The end of the `/pace` line says which priorities run at this moment:

| `/pace` ends with | Meaning |
|---|---|
| `crons run: all` | heavy mode |
| `crons run: vital, normal, optional` | normal mode |
| `crons run: vital, normal` | light mode |
| `crons run: vital only, lightly` | skip mode |
| `crons run: none` | a limit is used up: 5h or week at 100 % |

The tool JSON has the same per priority under `cron_jobs`, each `"runs"`, `"runs lightly"` or
`"waits"`:

```json
"cron_jobs": {"vital": "runs", "normal": "runs", "optional": "runs", "spare": "waits"}
```

## When usage can't be read

The mode is then normal (fail open), so vital, normal and optional jobs run and spare jobs wait. An
internal error in the gate counts the same way. See [Cron gate](cron-gate.md#failure-behaviour).
