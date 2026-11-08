# How it works

Each check does three things: read the two usage windows through Hermes, compare the weekly use with
a pace line, and pick one mode. The tool, `/pace`, `hermes pace` and the cron gate all run the same
check (`Pacer.payload` in `core.py`).

## What the plugin reads

The plugin calls Hermes' own usage fetcher, the one behind `/usage` and `hermes usage`:

```python
agent.account_usage.fetch_account_usage("anthropic", read_only=True)
```

Hermes resolves the Claude sign-in, asks Anthropic for the subscription's usage windows and returns a
snapshot. From that snapshot the plugin keeps, per window, the used percent and the reset time.

| Window | Matched by | Used for |
|---|---|---|
| 5-hour | first label containing "session" (Hermes calls it "Current session") | hard stop, light rule, heavy ceiling |
| Weekly | label containing "week" ("Current week"); an account-wide week beats a model-scoped one | pace line, hard stop, reserve |
| Others | everything else, such as a model-scoped Opus or Sonnet week | shown as `other_windows` in the JSON, not used for pacing |

Matching is case-insensitive. Reads are cached in memory for `cache_seconds` (default 60, range
0..3600, 0 = always fetch), keyed by the profile's `HERMES_HOME`. Each `hermes pace gate` run is a new
process, so the gate always reads fresh.

See [Security and privacy](security-and-privacy.md) for the network calls behind a read.

## The weekly target and the pace line

You keep `reserve_pct` of the week for your own chats (default 20). Agents and gated crons are paced
to the rest, the **weekly target**:

```
target = 100 − reserve_pct                                  (80 with the default reserve)
pace   = min(target, target × (fraction of the week gone) + pace_burst_pct)
```

- The fraction of the week gone is `1 − hours_to_reset / 168`, clamped to 0..1. The hours are rounded
  to 0.1 and the pace to 0.1 %.
- `pace_burst_pct` (default 10) is a head start, so a burst early in the week doesn't read as over
  pace. At the start of the week the pace is 10 %, not 0 %.
- The line ends at the target at the reset, and never goes above it.

With the default 20 % reserve:

| Time to reset | 7 d | 6 d | 5 d | 3.5 d | 2 d | 1 d | 0 |
|---|---|---|---|---|---|---|---|
| Pace | 10 % | 21.4 % | 32.9 % | 50 % | 67.1 % | 78.6 % | 80 % |

## The heavy line

Heavy work is back-loaded: heavy needs the week under a line that climbs exponentially from 0 at the
reset to the target at the next reset,

```
heavy_line = target × (e^(heavy_ramp × fraction gone) − 1) / (e^heavy_ramp − 1)
```

so the same 30 % week is normal mid-week and heavy two days before the reset. With the defaults
(`heavy_ramp` 3, target 80):

| Time to reset | 7 d | 5 d | 3.5 d | 2 d | 1 d | 12 h | 0 |
|---|---|---|---|---|---|---|---|
| Heavy line | 0 % | 5.7 % | 14.6 % | 31.5 % | 50.7 % | 63.8 % | 80 % |

A higher `heavy_ramp` pushes heavy room later and makes it steeper; `heavy_ramp` 0 is the straight rule
from before 0.3.0, `pace − pace_heavy_margin` (25). Heavy never reaches the target itself: the weekly
hard stop comes first.

The weekly hard stop sits at the target: once the week reaches it, every mode is skip, so agents and
gated crons don't spend into your reserve. ([Vital](job-priorities.md) cron jobs are the one
exception: pacing never stops them, so they may use the reserve until a limit is used up.)

## How a mode is picked

`decide()` in `pacing.py` checks the rules top to bottom. The first match wins:

1. **skip** if 5h ≥ `h5_skip_pct` (85).
2. **skip** if week ≥ the target (80).
3. **normal** if neither window could be read (fail open).
4. If the weekly reset time is unknown there is no pace line: **light** if 5h ≥ `h5_light_pct` (60) or
   week ≥ `no_pace_week_light_pct` (60), else **normal**. Never heavy.
5. **skip** if week > pace + `pace_skip_margin` (15).
6. **light** if 5h ≥ `h5_light_pct` (60).
7. **light** if week > pace.
8. **heavy** if 5h < `h5_heavy_max` (50) and either
   - the week resets within `heavy_reset_within_h` (24 h) and week ≤ target − `heavy_reset_margin`
     (80 − 25 = 55), or
   - week < the heavy line (below).
9. **normal** otherwise.

Defaults are in brackets; every one is a setting. Each rule has a stable id (`rule` in the JSON) and a
label for the human line, for example `far_over_pace` → "far over pace". [Modes](modes.md) lists them
all.

Two details change how the 5-hour number is read:

- **5-hour reset grace.** If the 5-hour window resets within `h5_fresh_within_min` (45 min), and
  `h5 + h5_burn_per_min × minutes_left` (0.5 % per minute) stays under the 5-hour hard stop, the
  window counts as 0 % for rules 4, 6 and 8. The hard stops (rules 1 and 2) always use the real
  number. See [FAQ](faq-troubleshooting.md#5-hour-grace).
- **Unknown numbers never trigger a rule.** A window that is missing, or has no used percent, is
  skipped by every rule that needs it.

## What happens with the mode

| Surface | What it does with the mode |
|---|---|
| `claude_pace` tool | Returns JSON with `mode`, `reason`, `guidance` and the numbers. The tool description asks the agent to call it before long or optional work and follow the guidance. |
| `/pace`, `hermes pace` | Print the one-line summary |
| `hermes pace gate` | Decides whether a cron job of a given priority runs, and passes the mode into its prompt ([Cron gate](cron-gate.md)) |

The plugin advises; it doesn't block anything by itself. A chat agent decides whether to call the
tool and follow its guidance. A cron job is held back only when its pre-run script is the gate.

## Fail open

When usage can't be read (no sign-in, endpoint down, a snapshot without the expected windows), the
mode is normal and the JSON carries `usage_error`. An internal error in a check also gives normal
(`rule: "error"`). The reasoning: this is a cost gate, and a broken read should not stop every
scheduled job. Details in [FAQ](faq-troubleshooting.md#usage_error).
