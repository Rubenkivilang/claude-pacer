# claude-pacer documentation

claude-pacer is a Hermes plugin that paces a Claude Pro/Max subscription across its 5-hour and weekly
usage windows. The repository [README](../README.md) is the short version. These pages cover each
part in detail.

| Page | What it covers |
|---|---|
| [Getting started](getting-started.md) | Install, the first `/pace`, gating one cron job, and checking that the gate works |
| [How it works](how-it-works.md) | What the plugin reads, the weekly pace line, and how each check picks a mode |
| [Modes](modes.md) | skip / light / normal / heavy: exact thresholds and what each mode means for a job |
| [Reserve](reserve.md) | `reserve_pct`, 20 % vs 30 % worked through, and the on track / at risk / in use statuses |
| [Job priorities](job-priorities.md) | vital / normal / optional / spare, how to set them, and what runs in each mode |
| [Cron gate](cron-gate.md) | `hermes pace gate`, the `wakeAgent: false` skip, and how the mode reaches the job's prompt |
| [FAQ and troubleshooting](faq-troubleshooting.md) | `usage_error`, a missing reset time, the 5-hour grace, config warnings, common questions |
| [Security and privacy](security-and-privacy.md) | What the plugin reads, which network calls happen, and why it stores no credentials |

All examples come from the plugin's own output. Most were produced with the test fixtures in
`tests/_plugin.py`, so the numbers are made up but the text is exactly what the code prints.
