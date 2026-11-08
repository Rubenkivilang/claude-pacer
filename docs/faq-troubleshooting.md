# FAQ and troubleshooting

## `usage_error`

The plugin couldn't read usage, so the mode is **normal** (fail open) and the JSON carries
`usage_error` with the reason. The line looks like this:

```
🟢 normal · usage unknown, fail open: no usage: no Claude Pro/Max sign-in in this profile, or the usage endpoint did not answer · crons run: vital, normal, optional
```

| `usage_error` | Cause | What to check |
|---|---|---|
| `no usage: no Claude Pro/Max sign-in in this profile, or the usage endpoint did not answer` | Hermes returned no snapshot | `hermes usage --provider anthropic` in the same profile; `hermes auth add anthropic` if there is no sign-in |
| `Anthropic account limits are only available for OAuth-backed Claude accounts.` | Hermes' own reason, passed through: the profile's Anthropic credential is an API key, not a Pro/Max sign-in | Sign in with the subscription: `hermes auth add anthropic` |
| `the usage snapshot has no 5-hour or weekly window` | The snapshot has no window labelled "session" or "week" | See [The labels changed](#the-labels-changed) |
| `usage read failed (<ExceptionType>: <message>)` | Hermes' fetcher raised; the message is cut at 120 characters | Usually transient; the next uncached read tries again |

In the `/pace` line the error is cut to about 120 characters; `/pace json` shows it in full. In a long-running
process a failed read is cached like a good one, for `cache_seconds` (60), so a burst of checks
doesn't hammer the endpoint.

While usage is unknown, vital, normal and optional cron jobs run and spare jobs wait.

## `pacing error, fail open`

`rule: "error"`: the check itself raised (a bug, not a usage problem). The mode is normal. The reason
names only the exception type, for example `claude-pacer error (KeyError), fail open`. The cron gate
has its own fallback with the same effect ([Cron gate](cron-gate.md#failure-behaviour)). Please
report it with `hermes pace --json` output.

## Missing reset time

If the weekly window has no reset time there is no pace line. The plugin then:

- still skips at 5h ≥ 85 % or week ≥ the target (80 %);
- gives **light** at 5h ≥ 60 % or week ≥ `no_pace_week_light_pct` (60 %), else **normal**;
- never gives heavy, because both heavy rules need the reset time;
- prints the reserve without a status, and `pace_pct`, `resets_in_h` and `reserve_status` are `null`.

```
🟢 normal · weekly reset time unknown, no pacing · week 30% · reserve 20% · 5h 10% (resets in 4h 54m) · crons run: vital, normal, optional
```

A missing 5-hour reset time only turns off the 5-hour grace below.

## 5-hour grace

A 5-hour window that is about to reset counts as fresh (0 %) for the light and heavy rules, when both
hold:

- it resets within `h5_fresh_within_min` (45) minutes, and
- `h5 + h5_burn_per_min × minutes_left` (0.5 % per minute) stays under `h5_skip_pct` (85).

The second condition means: even at a steady burn, the window can't hit the hard stop before it
resets. The hard stops always read the real number.

5h at 70 %, 20 minutes left: `70 + 0.5 × 20 = 80`, under 85, so it counts as fresh and the mode is
normal instead of light:

```
🟢 normal · on pace · week 30% ██████▒▒▒▒░░░░░░░░░░ pace 50% · reserve 20%: on track · resets in 3d 12h · 5h 70% (resets in 20 min, counted fresh) · crons run: vital, normal, optional
```

At 80 % with 20 minutes left, `80 + 10 = 90` reaches 85, so no grace:

```
🟡 light · 5h window running hot · week 30% ██████▒▒▒▒░░░░░░░░░░ pace 50% · reserve 20%: on track · resets in 3d 12h · 5h 80% (resets in 20 min) · crons run: vital, normal
```

When the grace applies and the real 5h number is at or above `h5_heavy_max` (50), the reason of a
light, normal or heavy decision starts with a note such as `5h 70% resets in 20 min, counted fresh; `.
The JSON has `five_hour.counted_fresh: true` whenever the grace applies.

## `config_warnings`

A setting that isn't a number in its range is ignored: the default is used and the tool JSON lists
the problem under `config_warnings`. The `/pace` line doesn't show it, so check with `/pace json`:

```json
"config_warnings": [
  "reserve_pct: 'lots' is not a number in 0..100, using 20",
  "h5_light_pct: 150 is not a number in 0..100, using 60"
]
```

Numbers given as strings (`"30"`) are accepted; `true`/`false`, NaN and infinity are not.

| Setting | Range |
|---|---|
| `heavy_reset_within_h` | 0..168 |
| `h5_fresh_within_min` | 0..300 |
| `cache_seconds` | 0..3600 |
| `heavy_ramp` | 0..10 |
| every other setting | 0..100 |

The ranges are checked one setting at a time; combinations are not. `h5_light_pct` above
`h5_skip_pct`, for example, is accepted and simply means light never fires from the 5-hour window.

## The labels changed

The plugin finds the windows by Hermes' labels: "session" for the 5-hour window and "week" for the
weekly one (case-insensitive). If a Hermes update renames them, the windows aren't found, the mode is
normal and `usage_error` says `the usage snapshot has no 5-hour or weekly window`. The plugin doesn't
guess.

## Common questions

**Does it stop my own chats?** No. In chat the plugin only advises: the agent calls `claude_pace`
and follows the guidance, or you check `/pace` yourself. The only thing it actually holds back is a
cron job whose pre-run script is the gate.

**Does a skipped cron run cost anything?** No tokens. The gate runs before the agent and, on
`"wakeAgent": false`, Hermes never starts it. The gate does one usage read per run.

**I changed a setting. When does it apply?** On the next check. Settings are read every time; only
usage reads are cached.

**Why is the pace 10 % right after the reset?** `pace_burst_pct` (10) is a head start so an early
burst doesn't count as over pace. Set it to 0 for a pure straight line.

**I have several profiles.** The pace is per Claude account: profiles signed in to the same account
see the same numbers and should usually share a reserve. Settings and the cache are per profile.

**What about the Opus or Sonnet weekly limit?** Model-scoped weeks are shown under `other_windows` in
the JSON but not used for pacing. The account-wide week is.

**Codex?** Not supported. Hermes' Codex fetcher has the same 5-hour/weekly shape, but the plugin only
asks for `anthropic`.

**`hermes pace` says the command doesn't exist.** Check `hermes plugins list` shows claude-pacer
enabled in this profile, and that `plugins.isolation` isn't `host` (Hermes skips plugin CLI commands
in the plugin host; `/pace` and the tool still work).

**The `claude_pace` tool doesn't show up in chat.** Start a new session; with a gateway, run
`hermes gateway restart` after enabling the plugin.

**How do I see what a job would do at 90 %?** `hermes pace --week 90 gate --priority <p>`. See
[Cron gate](cron-gate.md#testing-a-gate).
