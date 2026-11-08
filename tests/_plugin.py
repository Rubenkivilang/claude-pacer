"""Shared test helpers: load the plugin directory as the package ``claude_pacer`` (its directory name
has a hyphen), plus fixed times and fake Hermes usage snapshots. Stdlib only; nothing here touches
the network or Hermes."""

import importlib.util
import os
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Optional

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True  # no __pycache__ inside the plugin (catalog rule 17)

NOW = datetime(2026, 11, 5, 22, 0, tzinfo=UTC)


def load():
    mod = sys.modules.get("claude_pacer")
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(
        "claude_pacer", os.path.join(PLUGIN_DIR, "__init__.py"), submodule_search_locations=[PLUGIN_DIR])
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["claude_pacer"] = mod
    spec.loader.exec_module(mod)
    return mod


def hours(h):
    return NOW + timedelta(hours=h)


def minutes(m):
    return NOW + timedelta(minutes=m)


def window(label, used, reset=None, scope="account"):
    return SimpleNamespace(label=label, used_percent=used, reset_at=reset, scope=scope, detail=None)


def snapshot(h5: Optional[float] = 3.0, week: Optional[float] = 13.0, h5_reset_h: Optional[float] = 4.9,
             week_reset_h: Optional[float] = 158.6, extra=(), reason=None):
    """A stand-in for Hermes' AccountUsageSnapshot, shaped like the live Anthropic one."""
    windows = []
    if h5 is not None:
        windows.append(window("Current session", h5, hours(h5_reset_h) if h5_reset_h is not None else None))
    if week is not None:
        windows.append(window("Current week", week, hours(week_reset_h) if week_reset_h is not None else None))
    windows.extend(extra)
    return SimpleNamespace(provider="anthropic", source="oauth_usage_api", fetched_at=NOW, title="Account limits",
                           plan=None, windows=tuple(windows), details=(), unavailable_reason=reason)


def config(**values):
    """A ``ctx.get_config`` stand-in; cache off unless a test sets it."""
    values.setdefault("cache_seconds", 0)

    def get(key, default=None):
        return values.get(key, default)
    return get
