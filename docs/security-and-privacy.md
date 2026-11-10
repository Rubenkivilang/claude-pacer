# Security and privacy

Short version: the plugin's own code opens no connections, touches no credentials, writes no files
and sends no telemetry. Each check asks Hermes for the usage numbers that `/usage` already shows, and
Hermes makes the request with the sign-in it already holds.

## What the plugin reads

| Input | From | Used for |
|---|---|---|
| Usage snapshot: per window the label, used %, reset time and scope; plus provider, source and fetch time | `agent.account_usage.fetch_account_usage("anthropic", read_only=True)`, Hermes' usage fetcher | The pacing decision |
| Its own settings | `ctx.get_config`, i.e. `plugins.entries.claude-pacer.settings` in the profile's `config.yaml` | Thresholds, reserve, cache length |
| The profile's home path | `hermes_constants.get_hermes_home()`, falling back to the `HERMES_HOME` environment variable | Cache key only, so a gateway serving several profiles never mixes their numbers |

It reads nothing else: no other config keys, no files, no chat history, no other environment
variables. The snapshot's other fields (plan name, extra-usage credit details) are ignored.

The Hermes imports are lazy and happen only inside Hermes. The plugin is stdlib-only Python with no
dependencies to install.

## What it outputs

- **Tool result, `/pace`, `hermes pace`:** the mode, the numbers above, fixed reason, label and
  guidance strings, and, when they apply, `usage_error` (Hermes' reason or an exception type plus at
  most 120 characters of its message), `config_warnings` and `other_windows`.
- **Cron gate:** one JSON line with fixed text and numbers only (`usage_error` is not in it). Hermes
  puts it into the job's prompt as a fenced "Script Output" block.

All JSON output goes through `json.dumps`. The only user-supplied text echoed back is an unknown
`/pace` argument (to the user who typed it) and an invalid setting value quoted in
`config_warnings`.

## Network calls

The plugin makes none itself. Each check on a cache miss runs Hermes' Anthropic usage fetch, the same
one behind `/usage` and `hermes usage`:

| Request | When | Made by |
|---|---|---|
| `GET https://api.anthropic.com/api/oauth/usage` with the profile's Claude OAuth token (15 s timeout) | Every check that isn't served from the cache | Hermes |
| `POST` to Anthropic's OAuth token endpoint (`https://platform.claude.com/v1/oauth/token`, with `console.anthropic.com` as fallback) | Only when Hermes falls back to a borrowed Claude Code CLI login and its access token has expired | Hermes, while resolving the sign-in |

How often:

- In a long-running process (gateway, chat session) reads are cached in memory per profile for
  `cache_seconds`, default 60. Set 0 to read on every check; the maximum is 3600.
- Each `hermes pace` or `hermes pace gate` invocation is a new process, so it always reads once. A job
  gated every 30 minutes means 48 usage reads a day for that job.

## Credentials

The plugin's own code never reads, holds or writes a token or credential file. It doesn't receive
the token either: Hermes resolves the sign-in inside `fetch_account_usage` and returns only the
usage numbers.

How Hermes resolves it is Hermes' behaviour, not the plugin's, but a check triggers it, so here is
what happens:

- **A login Hermes owns** (`hermes auth add anthropic`) is used without a refresh on this path.
- **A borrowed Claude Code CLI login** (`~/.claude/.credentials.json`, or the macOS Keychain) is
  Hermes' fallback when the profile has no Hermes-owned login or API key; Hermes also prefers it
  when an OAuth token in the environment comes with a refreshable Claude Code file. If its access
  token has expired, Hermes refreshes it first: it posts the refresh token to the token endpoint and
  rewrites the Claude Code credentials file with the new pair (and mirrors it to the Keychain on
  macOS). `/usage` or the next model call would do exactly the same.
- `read_only=True` does not prevent that refresh. In current Hermes the flag only changes the Codex
  fetcher, so the plugin passes it but doesn't rely on it.

If you don't want a scheduled gate to be the process that rotates a Claude Code token, sign the
profile in with `hermes auth add anthropic`.

## Files, processes, telemetry

- No files written; nothing stored on disk. The cache is in memory and gone when the process ends.
- No shell commands, subprocesses, threads or background jobs. (The cache has a lock; that is all.)
- No telemetry, no analytics, no update checks.
- Nothing runs at import or registration time beyond registering the tool, `/pace` and `hermes pace`.

## What a caller can influence

- The `claude_pace` tool takes no arguments, so a model can't steer the request, the provider or the
  credential.
- `/pace` accepts only `json` and `help`.
- `hermes pace --week` / `--h5` (what-if) change only the numbers in that one answer, are marked
  `what_if: true`, and are available to whoever can run `hermes` on the machine.
- Settings come from the profile's `config.yaml`. Out-of-range values fall back to the defaults.
- Fail open: when usage can't be read, the mode is normal. That is an availability choice for a cost
  gate, not an access control. Nothing an outside party sends selects it.

## Security review

The plugin went through a hermes-security scan on 2026-11-06 (scan time 42 min, coverage complete,
15 of 15 files reviewed, OWASP Top 10:2025 categories reviewed or not applicable). Result:

| Severity | Findings |
|---|---|
| Critical / High / Medium / Low | 0 |
| Informational | 1 |

The informational finding: the manifest described each check as one read-only GET and said no
credential is refreshed, while Hermes can refresh an expired borrowed Claude Code login during the
check (the POST and file rewrite described above). The fix was in the documentation, not the code:
`plugin.yaml`, the README and the `usage.py` docstrings now describe the refresh, and a test
(`test_disclosure_names_the_refresh_a_check_can_trigger`) keeps them from going back to the old
wording. A scoped verify-fix scan of the patched files found no remaining issue.

That scan covered the code before the job priorities (`--priority`, version 0.2.0) were added. The
changes since (0.2.0 priorities, 0.3.0 back-loaded heavy line, 0.4.0 vital jobs that pacing never
stops, 0.4.1 a new default for `heavy_reset_margin`) are in `pacing.py`, `report.py`, `core.py` and
`cli.py`; they add no reads, network calls or file access. A second standard scan of version 0.4.0
(Nov 8, 2026, all 24 files, all 10 OWASP 2025 lanes) found nothing at any severity.

## Limits worth knowing

- The plugin trusts Hermes' snapshot. If Hermes reports wrong numbers, the pacing is wrong too.
- `plugins.isolation: host` runs the tool and `/pace` in the plugin host, but Hermes skips plugin CLI
  commands there, so `hermes pace` and the cron gate need the default in-process mode.
