# claude-pacer

Pace a Claude Pro/Max subscription across its week. The plugin reads the two usage windows Hermes
already shows in `/usage` (the 5-hour window and the weekly one), draws a pace line to the share of
the week your agents may use (everything but the reserve you keep for your own chats, 20 % unless you
set `reserve_pct`) and answers with a mode, **skip**, **light**, **normal** or **heavy**, plus a
one-line reason.

- The agent calls the **`claude_pace`** tool before big or optional work and follows its guidance.
- You type **`/pace`** in any chat surface, or run **`hermes pace`**, and get one line:

  ```
  🟢 normal · on pace · week 13% ███░░░░░░░░░░░░░░░░░ pace 14.5% · reserve 20%: on track · resets in 6d 15h · 5h 3% (resets in 4h 54m) · crons run: vital, normal, optional
  ```

- A cron job gates on **`hermes pace gate --priority vital|normal|optional|spare`**. A job that should
  wait gets `{"wakeAgent": false, ...}` and Hermes skips the run without spending a token; otherwise the
  mode and reason go into the job's prompt. In skip mode only **vital** jobs keep running, lightly;
  the rest wait. Pacing never stops a vital job: only a used-up limit does.
- You choose how much of the week stays yours with one setting, **`reserve_pct`**. Agents and gated
  crons are paced to the rest and stop when the week reaches it. Vital crons may use it (see below).

Why a plugin and not a skill: the numbers sit behind the Claude sign-in. A prompt or skill can't read
them without handling the token. This plugin asks Hermes, which already holds the sign-in, and never
sees it.

## Documentation

The [docs](docs/README.md) folder has a page per topic:

- [Getting started](docs/getting-started.md): install, the first `/pace`, gate one cron job, check it works
- [How it works](docs/how-it-works.md): what is read, the pace line, how a mode is picked
- [Modes](docs/modes.md): exact thresholds and what each mode means for a job
- [Reserve](docs/reserve.md): `reserve_pct`, 20 % vs 30 %, the reserve statuses
- [Job priorities](docs/job-priorities.md): vital / normal / optional / spare and what runs when
- [Cron gate](docs/cron-gate.md): `hermes pace gate`, the skip, the mode in the job's prompt
- [FAQ and troubleshooting](docs/faq-troubleshooting.md): `usage_error`, missing reset time, 5h grace
- [Security and privacy](docs/security-and-privacy.md): reads, network calls, credentials

## How the pacing works

The weekly target is everything but your reserve: `100 − reserve_pct`, so 80 % with the default 20 %
reserve. The weekly window gets a pace line, a straight line from the reset to that target at the
next reset, with a 10-point head start so an early-week burst doesn't read as over pace:

```
target = 100 − reserve                        (80 with the default reserve of 20)
pace   = min(target, target × (fraction of the week gone) + 10)
```

Heavy work is back-loaded. Heavy mode needs the week under a **heavy line** that climbs
exponentially, from 0 at the reset to the target at the next reset, so there is little heavy room
early in the week and a lot in its last days (`heavy_ramp` sets how steep):

```
heavy_line = target × (e^(3 × fraction gone) − 1) / (e^3 − 1)    (heavy_ramp 3)
```

| Time to reset | 7 d | 5 d | 3.5 d | 2 d | 1 d | 12 h | 0 |
|---|---|---|---|---|---|---|---|
| Heavy line | 0 % | 5.7 % | 14.6 % | 31.5 % | 50.7 % | 63.8 % | 80 % |

First match wins (defaults shown, all of them settings):

| Mode | When | Guidance the agent gets |
|---|---|---|
| skip | 5h ≥ 85 %, week ≥ the target (80 %: the reserve is reached), or week more than 15 points over pace | Do only what was asked, keep it short, defer optional work |
| light | 5h ≥ 60 %, or week over pace | Short turns, no subagents or long tool loops |
| heavy | 5h < 50 % and either the week is under the heavy line, or it resets within 24 h with use 25 or more points under the target (≤ 55 %) | Spare capacity that would go unused: run the big jobs |
| normal | everything else | Work normally |

- **5-hour reset grace.** A 5-hour window that resets within 45 minutes counts as fresh for the light
  and heavy rules, as long as 0.5 % per minute until the reset can't reach the 5-hour hard stop. The
  hard stops always read the raw numbers.
- **Fail open.** If usage can't be read (no Claude sign-in, endpoint down, an odd snapshot), the mode is
  normal and the result says why (`usage_error`). If the weekly reset time is missing there is no pace
  line: light at 5h ≥ 60 % or week ≥ 60 %, never heavy.

## Keep a reserve

`reserve_pct` (default 20) is the share of the weekly allowance you keep unused for yourself, for your
own chats. Agents and cron jobs are paced to the rest: the pace line ends at `100 − reserve_pct` at
the weekly reset, and once the week reaches that line every mode is skip, so a gated cron stops before
it touches your share. The one exception is a cron job you gate as **vital**: pacing never stops it,
so it keeps running, lightly, into the reserve, and waits only once a limit is used up (the 5-hour
window or the week at 100 %, where Anthropic refuses the run anyway). A job your setup can't do
without is worth more than unused headroom, but it does come out of your share: gate only what really
can't wait as vital. The weekly window counts your own chats too, so the reserve is what is left for
them when the agents have had theirs.

```bash
hermes config set plugins.entries.claude-pacer.settings.reserve_pct 30   # keep 30 %, agents get 70 %
```

`/pace` and `hermes pace` say how it stands, for example `reserve 30%: on track`:

| Status | When |
|---|---|
| on track | the week is at or under the pace line, so at this pace normal and optional jobs leave the reserve alone (vital ones may still dip into it) |
| at risk | the week is over the pace line: agents get light (or skip) until the line catches up |
| in use | the week has reached the target: agents and gated crons skip, vital ones run on, lightly, and what they use comes out of your reserve |

The tool result carries the same as `week.reserve_pct` and `week.reserve_status`.

## Install

Needs Hermes ≥ 0.22.7 and a Claude Pro/Max sign-in in the profile (`hermes auth add anthropic`). If
`hermes usage --provider anthropic` shows "Current session" and "Current week", the plugin has its data.

```bash
hermes plugins install Rubenkivilang/claude-pacer --enable   # https://github.com/Rubenkivilang/claude-pacer
# or from a local copy, into this profile's $HERMES_HOME/plugins:
plugins="$(dirname "$(hermes config path)")/plugins"
mkdir -p "$plugins" && cp -r claude-pacer "$plugins/" && hermes plugins enable claude-pacer
```

Then `hermes pace` in a terminal, `/pace` in a chat (new sessions pick up the tool).

## Gate a cron job

Cron only runs scripts from the profile's own scripts folder, `$HERMES_HOME/scripts`
(`~/.hermes/scripts` for the default profile, `~/.hermes/profiles/<name>/scripts` for a named one),
so add a two-line shim there:

```bash
scripts="$(dirname "$(hermes config path)")/scripts"    # $HERMES_HOME/scripts of this profile
mkdir -p "$scripts"
printf '#!/usr/bin/env bash\nexec hermes pace gate "$@"\n' > "$scripts/claude-pace-gate.sh"
printf '#!/usr/bin/env bash\nexec hermes pace gate --priority vital "$@"\n' > "$scripts/claude-pace-gate-vital.sh"
hermes cron create "0 */2 * * *" "Work the backlog. Size the work to budget_mode." \
  --name backlog --script claude-pace-gate.sh
hermes cron create "*/30 * * * *" "Check the shop's order inbox. Size the work to budget_mode." \
  --name orders --script claude-pace-gate-vital.sh
```

For a named profile add `-p <name>` to `hermes config path` and `hermes cron create`. The shim needs
no flag: cron runs it with that profile's `HERMES_HOME`.

### Job priorities

Not every job can wait. `--priority` says how much a job matters, and so when it waits (one shim
per priority, since a cron job's `script` takes no arguments; put the flag on the `exec` line):

| `--priority` | The job runs in | For |
|---|---|---|
| `vital` | every mode, in skip lightly; pacing never stops it, it may use the reserve and waits only while the 5-hour or weekly limit is used up (100 %) | jobs your setup can't do without: order or inbox watchers, alerts, monitors |
| `normal` (default) | light, normal, heavy: waits in skip mode | regular work |
| `optional` | normal, heavy: also waits while the week runs hot | nice-to-have jobs: digests, cleanups, low-priority research |
| `spare` | heavy only | use-it-or-lose-it work: the big backlog, refactors, long research |

Hermes reads only `wakeAgent`; the rest of the line says which priority the job has and why it ran or
waited. A job that waits:

```json
{"wakeAgent": false, "priority": "normal", "why": "normal job, skip mode (far over pace): waits; only vital jobs run in skip mode"}
```

A job that runs gets the budget in its prompt, for example:

```json
{"wakeAgent": true, "priority": "normal", "why": "normal job, light mode (over pace): runs", "context": {"budget_mode": "light",
 "budget_reason": "over pace; week 55% vs pace 50%, resets in 84.0h", "budget_guidance": "Running hot: ...", "five_hour_used_pct": 10,
 "week_used_pct": 55, "week_resets_in_h": 84.0, "week_pace_pct": 50.0}}
```

A vital job that runs in skip mode keeps `budget_mode: "skip"` (the real mode) and gets its own guidance:
it may be spending your reserve, so it does only the essential part, keeps turns short, no subagents or
long tool loops. In light mode it gets light's guidance, like every job that runs there. `/pace` ends
with which priorities run right now, for example `crons run: vital only, lightly` in skip mode, and the
tool result carries the same per priority as `cron_jobs` (`runs`, `runs lightly` or `waits`).

When usage can't be read the mode is normal, so vital, normal and optional jobs still run (fail open)
and spare jobs wait. An internal error in the gate counts the same way. Given valid arguments the gate
always exits 0 and prints exactly one line of strict JSON; a bad one (`--priority urgent`, `--week nan`)
is an argparse error, exit 2.

Test a gate without waiting for a bad week: `hermes pace --week 90 gate` prints a `"wakeAgent": false`
line, and `hermes pace --week 90 gate --priority vital` a `"wakeAgent": true` one.

## Settings

Under `plugins.entries.claude-pacer.settings` in `config.yaml`, in the Desktop's Settings → Plugins
form, or from a terminal:

```bash
hermes config set plugins.entries.claude-pacer.settings.h5_light_pct 50
```

Settings are read on every check, so a change applies at once. A value that isn't a number in range
falls back to its default and shows up in the tool result as `config_warnings`.

| Key | Default | Meaning |
|---|---|---|
| `reserve_pct` | 20 | Share of the week kept unused for you; agents are paced to 100 − this and skip once the week reaches it (vital cron jobs may use it) |
| `pace_burst_pct` | 10 | Head start on the pace line for early-week bursts |
| `pace_skip_margin` | 15 | Skip when the week is more than this many points over pace |
| `heavy_ramp` | 3 | How steeply the heavy line climbs toward the reset (higher: heavy room comes later); 0 = the straight rule below |
| `pace_heavy_margin` | 25 | With `heavy_ramp` 0 only: heavy when the week is more than this many points under pace |
| `h5_skip_pct` | 85 | 5-hour hard stop |
| `h5_light_pct` | 60 | Light at or above this 5-hour use |
| `h5_heavy_max` | 50 | Heavy only while the 5-hour window is under this |
| `heavy_reset_within_h` | 24 | Use-it-or-lose-it window before the weekly reset (hours) ... |
| `heavy_reset_margin` | 25 | ... while the week is at least this many points under the target (55 % by default) |
| `h5_fresh_within_min` | 45 | A 5-hour window resetting this soon counts as fresh ... |
| `h5_burn_per_min` | 0.5 | ... unless this burn per minute would hit the 5-hour hard stop first |
| `no_pace_week_light_pct` | 60 | No weekly reset time known: light at or above this weekly use |
| `cache_seconds` | 60 | Reuse a usage read this long (0 = fetch on every check); memory only |

A vital job's stop is not a setting: it is the real limit, 100 %. 0.3.0's `vital_h5_stop_pct` and
`vital_week_stop_pct` are no longer used; a config that still sets them gets a `config_warnings` entry.

## Surfaces in detail

- **`claude_pace`** (tool, no arguments) returns JSON: `mode`, `reason`, `guidance`, `five_hour` and
  `week` (`used_pct`, `resets_at`, `resets_in_h`; the week also has `pace_pct`, `reserve_pct` and
  `reserve_status`; the 5-hour window has `counted_fresh`), `rule`, `cron_jobs` (per priority: `runs`,
  `runs lightly` or `waits`), `summary` (the human line), `source`, `fetched_at`, plus `usage_error`,
  `other_windows` (model-scoped weeks such as Opus) or `config_warnings` when they apply.
- **`/pace`** prints the line; `/pace json` prints the full result.
- **`hermes pace`** prints the line; `--json` the full result; `--week PCT` / `--h5 PCT` answer
  "what if I were at PCT % right now" (reset times stay real); `gate [--priority P]` is the cron gate.

## Disclosure

- **Network:** each check runs Hermes' own Anthropic usage fetch, the same one `/usage` and
  `hermes usage` run: the plugin calls `agent.account_usage.fetch_account_usage("anthropic",
  read_only=True)` and Hermes makes the requests, not the plugin. That is one GET to Anthropic's OAuth
  usage endpoint (`https://api.anthropic.com/api/oauth/usage`), preceded by one POST to Anthropic's
  OAuth token endpoint only when the sign-in is a borrowed Claude Code login whose access token has
  expired (see Credentials). Reads are cached in memory for 60 s per profile; each `hermes pace gate`
  run is a new process, so it always reads. The plugin opens no connections of its own.
- **Credentials:** the plugin's own code never reads, holds or writes a token or credential file.
  Hermes resolves the profile's Claude sign-in its usual way. A login Hermes owns (`hermes auth add
  anthropic`) is read without a refresh. A borrowed Claude Code CLI login whose access token has
  expired is refreshed first: Hermes posts its refresh token and rewrites the Claude Code credentials
  file, exactly as `/usage` or the next model call would. `read_only=True` does not prevent this: in
  current Hermes it only changes the Codex fetcher, so the plugin does not rely on it.
- **Files and processes:** it writes no files, runs no shell commands or subprocesses, starts no
  threads or background jobs, and sends no telemetry. It reads only its own settings.
- **`plugins.isolation: host`:** the tool and `/pace` work in the plugin host; Hermes skips plugin CLI
  commands there, so `hermes pace` and the cron gate need the default in-process mode.

## Development

```bash
cd claude-pacer
python3 -B -m unittest            # stdlib only, no network, no Hermes needed (-B: no __pycache__)
hermes plugins validate .
hermes plugins doctor . --ci
```

Layout: `pacing.py` (pure pacing math), `usage.py` (reads Hermes' usage snapshot), `report.py`
(tool JSON, the line and its bar, the gate document), `core.py` (one `Pacer` behind every surface),
`cli.py` (`hermes pace` arguments), `__init__.py` (registration only).

## Limits

- Claude only. Codex has the same 5-hour/weekly shape in Hermes' fetcher but isn't wired up.
- Window matching uses Hermes' labels ("Current session", "Current week"). If Hermes renames them, the
  plugin reads usage as unknown and fails open rather than guessing.
- The pace is per Claude account. Profiles signed in to the same account see the same numbers.
- Until [NousResearch/hermes-agent#139916](https://github.com/NousResearch/hermes-agent/pull/139916)
  lands, Hermes reads an Anthropic usage window at 1 % as 100 %, so right after a 5-hour or weekly
  reset claude-pacer can report skip until the window passes 1 %.

## License

MIT, see [LICENSE](LICENSE).
