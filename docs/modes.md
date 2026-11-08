# Modes

Every check ends in one of four modes, from least to most generous: **skip**, **light**, **normal**,
**heavy**. The mode comes with a reason (the numbers behind it), a rule id, and a guidance sentence
for the agent.

## Thresholds

Defaults shown; the rules are checked in this order and the first match wins
([How it works](how-it-works.md#how-a-mode-is-picked)). "Target" is `100 − reserve_pct`, 80 by default.

| Mode | Rule id | Fires when (defaults) | Label in `/pace` |
|---|---|---|---|
| skip | `h5_hard_stop` | 5h ≥ 85 % (`h5_skip_pct`) | 5h hard stop reached |
| skip | `week_hard_stop` | week ≥ target, 80 % | reserve reached |
| normal | `usage_unknown` | neither window could be read | usage unknown, fail open |
| light | `no_pace_light` | no weekly reset time, and 5h ≥ 60 % or week ≥ 60 % (`no_pace_week_light_pct`) | running hot (weekly reset time unknown) |
| normal | `no_pace` | no weekly reset time, under those limits | weekly reset time unknown, no pacing |
| skip | `far_over_pace` | week > pace + 15 (`pace_skip_margin`) | far over pace |
| light | `h5_hot` | 5h ≥ 60 % (`h5_light_pct`) | 5h window running hot |
| light | `over_pace` | week > pace | over pace |
| heavy | `reset_soon_spare` | 5h < 50 % (`h5_heavy_max`), week resets within 24 h (`heavy_reset_within_h`), week ≤ target − 25 (`heavy_reset_margin`), so ≤ 55 % | spare capacity, week resets soon |
| heavy | `under_pace` | 5h < 50 %, week < the heavy line (exponential, 0 % at the reset to 80 % at the next; `heavy_ramp`, see [How it works](how-it-works.md#the-heavy-line)) | well under pace |
| normal | `on_pace` | everything else | on pace |
| normal | `error` | the check itself failed | pacing error, fail open |

Comparisons are exactly as written: the hard stops and light thresholds include the limit (≥), "over
pace" and "far over pace" don't (>). For heavy, `5h < 50` and `week < heavy line` are strict, while
"resets within 24 h" (≤ 24 h) and `week ≤ target − 25` include the limit.

`under_pace` is rare early in the week (the heavy line is 5.7 % with 5 days left, 14.6 % mid-week) and
common late (50.7 % with a day left), so heavy work lands in the week's last days.

## What each mode means

| Mode | Guidance the agent gets (verbatim) | Gated cron jobs that run |
|---|---|---|
| skip | "Over budget: do only what was explicitly asked, keep it short, and defer optional or background work until the windows reset." | vital only, lightly (pacing never stops vital jobs; they may use the reserve) |
| light | "Running hot: keep turns short, avoid subagents and long tool loops, and finish or park big tasks." | vital, normal |
| normal | "On pace: work normally." | vital, normal, optional |
| heavy | "Spare capacity that would otherwise go unused: a good time for big jobs (subagents, long research, refactors, backlog)." | all four |

A vital job that runs in skip mode gets its own guidance instead of the skip text: "Over budget, but
this job is vital, so it runs even into the user's reserve: do only its essential part, keep turns
short, and skip subagents, long tool loops and optional extras." In light mode it gets light's
guidance. No mode stops a vital job; only a used-up limit (5h or week at 100 %) does. See
[Job priorities](job-priorities.md).

## Examples

All from the test fixtures, default settings, 3 d 12 h (84 h) to the weekly reset, so pace is 50 %.

**normal**, week on the line:

```
🟢 normal · on pace · week 50% ██████████░░░░░░░░░░ pace 50% · reserve 20%: on track · resets in 3d 12h · 5h 10% (resets in 4h 54m) · crons run: vital, normal, optional
```

**light**, week 5 points over pace:

```
🟡 light · over pace · week 55% ██████████▓░░░░░░░░░ pace 50% · reserve 20%: at risk · resets in 3d 12h · 5h 10% (resets in 4h 54m) · crons run: vital, normal
```

**light**, week fine but the 5-hour window hot:

```
🟡 light · 5h window running hot · week 30% ██████▒▒▒▒░░░░░░░░░░ pace 50% · reserve 20%: on track · resets in 3d 12h · 5h 65% (resets in 4h 54m) · crons run: vital, normal
```

**skip**, 16 points over pace:

```
🔴 skip · far over pace · week 66% ██████████▓▓▓░░░░░░░ pace 50% · reserve 20%: at risk · resets in 3d 12h · 5h 10% (resets in 4h 54m) · crons run: vital only, lightly
```

**heavy**, under the 14.6 % heavy line mid-week:

```
🔵 heavy · well under pace · week 10% ██▒▒▒▒▒▒▒▒░░░░░░░░░░ pace 50% · reserve 20%: on track · resets in 3d 12h · 5h 10% (resets in 4h 54m) · crons run: all
```

**heavy**, the evening before the reset (20 h left) with 40 % used: the remaining capacity would be
lost at the reset.

```
🔵 heavy · spare capacity, week resets soon · week 40% ████████▒▒▒▒▒▒▒▒░░░░ pace 80% · reserve 20%: on track · resets in 20h · 5h 10% (resets in 4h 54m) · crons run: all
```

## Changing thresholds

Every threshold above is a setting under `plugins.entries.claude-pacer.settings`:

```bash
hermes config set plugins.entries.claude-pacer.settings.h5_light_pct 50
```

Settings are read on every check, so a change applies to the next one. A value that isn't a number
in its range falls back to the default; the accepted ranges are in the
[FAQ](faq-troubleshooting.md#config_warnings). Every setting with its meaning is in the
[README](../README.md#settings).
