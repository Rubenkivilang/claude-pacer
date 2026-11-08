"""claude-pacer: pace a Claude Pro/Max subscription's 5-hour and weekly windows.

Registers the ``claude_pace`` tool, the ``/pace`` slash command and ``hermes pace`` (whose ``gate``
subcommand is a cron pre-run gate). Usage comes from Hermes' own usage fetcher; the plugin never
handles a credential. Nothing here runs at import or registration time beyond wiring.
"""

from __future__ import annotations

from . import cli
from .core import TOOL_NAME, TOOL_SCHEMA, TOOLSET, Pacer

__all__ = ["register", "Pacer"]


def register(ctx) -> None:
    pacer = Pacer(get_config=getattr(ctx, "get_config", None))
    ctx.register_tool(name=TOOL_NAME, toolset=TOOLSET, schema=TOOL_SCHEMA, handler=pacer.tool,
                      description="Claude Pro/Max usage pace: skip / light / normal / heavy", emoji="⏱️")
    ctx.register_command("pace", pacer.slash, description="Claude usage pace: mode, week vs pace, 5h window",
                         args_hint="[json|help]")
    ctx.register_cli_command(
        name="pace", help="Claude usage pace (one line, --json, or a cron gate)",
        setup_fn=cli.setup_parser, handler_fn=pacer.cli,
        description="Show where this Claude Pro/Max subscription stands against its weekly pace, or gate a "
                    "cron job on it: `hermes pace`, `hermes pace --json`, `hermes pace gate [--priority P]`.")
