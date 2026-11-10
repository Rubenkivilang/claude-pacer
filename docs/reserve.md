# Reserve

`reserve_pct` is the share of the weekly allowance you keep for yourself. Agents and gated cron jobs
are paced to the rest and stop when they reach it; only cron jobs gated as vital go on into it (see
[Vital jobs and the reserve](#vital-jobs-and-the-reserve)). It is the one setting most users change.

```bash
hermes config set plugins.entries.claude-pacer.settings.reserve_pct 30   # keep 30 %, agents get 70 %
```

Default 20, range 0..100. The setting is read on every check, so it applies at once.

## What it changes

The reserve sets the **weekly target**, `100 − reserve_pct`, and three things follow from it:

| | Formula | Reserve 20 % | Reserve 30 % |
|---|---|---|---|
| Weekly target (where the pace line ends) | 100 − reserve | 80 % | 70 % |
| Weekly hard stop (skip from here on; vital jobs go on) | = target | 80 % | 70 % |
| Use-it-or-lose-it ceiling (heavy in the last 24 h) | target − `heavy_reset_margin` | 50 % | 40 % |

The pace line is `min(target, target × fraction_of_week_gone + 10)`, so a bigger reserve makes it
climb more slowly:

| Time to reset | 7 d | 6 d | 5 d | 3.5 d | 2 d | 1 d | 0 |
|---|---|---|---|---|---|---|---|
| Pace, reserve 20 % | 10 % | 21.4 % | 32.9 % | 50 % | 67.1 % | 78.6 % | 80 % |
| Pace, reserve 30 % | 10 % | 20 % | 30 % | 45 % | 60 % | 70 % | 70 % |

With 30 % the line hits its 70 % cap one day before the reset (`70 × 6/7 + 10 = 70`).

The weekly window counts all use on the account, your own chats included. The reserve is not a
separate bucket: it is what is left for you once agents stop at the target, minus whatever vital cron
jobs use after that.

## Example: the same week, two reserves

Week at 50 %, 84 hours (3 d 12 h) to the reset, 5-hour window at 10 %.

With the default 20 % reserve the pace is 50 %, the week is on the line:

```
🟢 normal · on pace · week 50% ██████████░░░░░░░░░░ pace 50% · reserve 20%: on track · resets in 3d 12h · 5h 10% (resets in 4h 54m) · crons run: vital, normal, optional
```

With `reserve_pct 30` the pace is 45 %, so the same 50 % is over pace:

```
🟡 light · over pace · week 50% █████████▓░░░░░░░░░░ pace 45% · reserve 30%: at risk · resets in 3d 12h · 5h 10% (resets in 4h 54m) · crons run: vital, normal
```

The agent gets the light guidance, and an `optional` cron job now waits:

```json
{"wakeAgent": false, "priority": "optional", "why": "optional job, light mode (over pace): waits; optional jobs run again once the week is back on pace"}
```

Above 60 % in the same spot (more than 15 points over the 45 % pace) the 30 % reserve gives skip.
With the 20 % reserve, skip starts above 65 %.

## Reserve status

`/pace` and `hermes pace` print the reserve with a status, for example `reserve 20%: on track`. The
tool JSON carries the same as `week.reserve_pct` and `week.reserve_status`.

| Status | When | What agents and crons do |
|---|---|---|
| on track | week ≤ pace | At this pace normal and optional jobs leave the reserve alone; vital ones may still dip into it |
| at risk | week > pace (and under the target) | Light or skip until the pace line catches up |
| in use | week ≥ target | Skip. Only vital crons run, lightly, and what they use comes out of your reserve |
| (none) | the weekly reset time is unknown, so there is no pace line | The line shows `reserve 20%` with no status |

The reserve reached, default settings, 10 hours before the reset:

```
🔴 skip · reserve reached · week 80% ████████████████░░░░ pace 80% · reserve 20%: in use · resets in 10h · 5h 10% (resets in 4h 54m) · crons run: vital only, lightly
```

## Vital jobs and the reserve

Pacing never stops a cron job gated with `--priority vital`. After the week reaches the target it keeps
running, lightly, and what it spends comes out of your reserve. It waits only when a limit is really
used up: the 5-hour window or the week at 100 %, where Anthropic refuses the run anyway. That stop is
not a setting.

This is by design: a job your setup can't do without matters more than unused headroom. The price:
the reserve is protected from agents and from normal, optional and spare jobs, not from vital ones.
If you want all of it kept, don't gate anything as vital; if you gate something as vital,
keep it small (its guidance in skip mode tells it to do only the essential part). See
[Job priorities](job-priorities.md).

0.3.0 stopped vital jobs at `vital_week_stop_pct` (98) and `vital_h5_stop_pct` (95). Both settings are
no longer used; a config that still sets one gets a warning in the tool result:

```json
"config_warnings": ["vital_week_stop_pct: no longer used, vital jobs stop only when a limit is used up (100%)"]
```

## Choosing a value

- `0` gives agents the whole week; the hard stop moves to 100 %, the real limit.
- `20` (default) keeps a fifth for your own chats.
- Raise it if you often find the week used up when you sit down to work yourself. Lower it if the
  week regularly resets with a lot unused (`/pace` near the reset shows how much).
