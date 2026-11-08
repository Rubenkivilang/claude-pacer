# Getting started

This page takes you from nothing installed to one cron job that waits when the week runs hot.

## Requirements

- Hermes 0.22.7 or later (`requires_hermes: ">=0.22.7"` in `plugin.yaml`).
- A Claude Pro/Max sign-in that Hermes can use in the profile. Either a login Hermes owns
  (`hermes auth add anthropic`) or a Claude Code CLI login on the same machine, which Hermes uses
  as a fallback.
- The default plugin mode (`plugins.isolation` not set to `host`). In host isolation the tool and
  `/pace` still work, but Hermes skips plugin CLI commands, so `hermes pace` and the cron gate are
  not available.

Check that Hermes can see the usage windows before you install anything:

```bash
hermes usage --provider anthropic
```

If that output shows "Current session" and "Current week", the plugin has its data. The plugin
matches windows by those labels (see [FAQ](faq-troubleshooting.md#the-labels-changed)).

## Install

From GitHub ([Rubenkivilang/claude-pacer](https://github.com/Rubenkivilang/claude-pacer)):

```bash
hermes plugins install Rubenkivilang/claude-pacer --enable
```

From a local copy, into the active profile's `$HERMES_HOME/plugins`:

```bash
plugins="$(dirname "$(hermes config path)")/plugins"
mkdir -p "$plugins" && cp -r claude-pacer "$plugins/" && hermes plugins enable claude-pacer
```

`hermes config path` prints the active profile's `config.yaml`, so its folder is `$HERMES_HOME`. For
a named profile add `-p <name>`. If a gateway is running, restart it (`hermes gateway restart`) so
chat surfaces load the plugin. New chat sessions pick up the `claude_pace` tool.

## First check

In a terminal:

```bash
hermes pace
```

In any chat surface, type `/pace`. Both print one line:

```
🟢 normal · on pace · week 13% ███░░░░░░░░░░░░░░░░░ pace 14.5% · reserve 20%: on track · resets in 6d 15h · 5h 3% (resets in 4h 54m) · crons run: vital, normal, optional
```

Reading it left to right:

| Part | Meaning |
|---|---|
| `🟢 normal · on pace` | The mode and the rule that picked it ([Modes](modes.md)) |
| `week 13% ███░░… pace 14.5%` | Weekly use, a 20-cell bar, and how much of the week may be used by now ([How it works](how-it-works.md)) |
| `reserve 20%: on track` | Your reserve and how it stands ([Reserve](reserve.md)) |
| `resets in 6d 15h` | Time to the weekly reset |
| `5h 3% (resets in 4h 54m)` | The 5-hour window and its reset |
| `crons run: vital, normal, optional` | Which gated cron priorities run right now ([Job priorities](job-priorities.md)) |

The bar uses one cell per 5 %: `█` used within pace, `▓` used beyond pace, `▒` allowed by now but not
used yet, `░` the rest of the week.

Other forms of the same check:

```bash
hermes pace --json          # the full result, the same JSON the claude_pace tool returns
hermes pace --week 70       # what-if: pretend the week is at 70 % (reset times stay real)
hermes pace --h5 90         # what-if for the 5-hour window
```

In chat, `/pace json` prints the full result and `/pace help` the usage line.

## Gate one cron job

Hermes cron runs pre-run scripts only from the profile's own `$HERMES_HOME/scripts` folder, and a
job's `script` takes no arguments. So the gate goes into a two-line shim there:

```bash
scripts="$(dirname "$(hermes config path)")/scripts"
mkdir -p "$scripts"
printf '#!/usr/bin/env bash\nexec hermes pace gate "$@"\n' > "$scripts/claude-pace-gate.sh"
hermes cron create "0 */2 * * *" "Work the backlog. Size the work to budget_mode." \
  --name backlog --script claude-pace-gate.sh
```

This job has the default priority, `normal`: it runs in light, normal and heavy mode and waits in
skip mode. Cron runs a `.sh` script with bash, so the shim does not need to be executable. For other
priorities, see [Job priorities](job-priorities.md).

## Check that it works

1. Run the shim the way cron will. It prints exactly one JSON line and exits 0:

   ```bash
   bash "$scripts/claude-pace-gate.sh"; echo "exit $?"
   ```

   ```
   {"wakeAgent": true, "priority": "normal", "why": "normal job, light mode (over pace): runs", "context": {"budget_mode": "light", "budget_reason": "over pace; week 41% vs pace 37.1%, resets in 111.1h", "budget_guidance": "Running hot: keep turns short, avoid subagents and long tool loops, and finish or park big tasks.", "five_hour_used_pct": 42.0, "week_used_pct": 41.0, "week_resets_in_h": 111.1, "week_pace_pct": 37.1}}
   exit 0
   ```

2. Force a bad week without waiting for one. `--week` goes before `gate`:

   ```bash
   hermes pace --week 90 gate
   ```

   ```
   {"wakeAgent": false, "priority": "normal", "why": "normal job, skip mode (reserve reached, what-if): waits; only vital jobs run in skip mode", "what_if": true}
   ```

   `"wakeAgent": false` is the line that makes cron skip the agent run.

3. Trigger the real job and look at its run record:

   ```bash
   hermes cron list                 # find the job id
   hermes cron run <job_id>         # runs on the next scheduler tick
   hermes cron runs <job_id>        # the run history
   ```

   When the gate says wait, the run's output reads "Script gate returned `wakeAgent=false` — agent
   skipped." and nothing is delivered. When it runs, the gate line is at the top of the prompt the
   agent gets (see [Cron gate](cron-gate.md)).

## Next

- Pick a reserve that fits how much you chat yourself: [Reserve](reserve.md).
- Give jobs that must not stop a `vital` priority: [Job priorities](job-priorities.md).
- Tune thresholds: every setting is listed in the [README](../README.md#settings).
