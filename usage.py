"""Read the Claude usage windows through Hermes, never through a token.

Hermes already fetches the subscription's usage windows for ``/usage`` and ``hermes usage``:
``agent.account_usage.fetch_account_usage("anthropic")`` resolves the profile's Claude sign-in
itself and returns a snapshot (windows with label, used percent and reset time). This module calls
that helper (the one the plugin guide names for usage panels), so the plugin's own code never reads,
holds or writes a credential. Resolving the sign-in is Hermes' job, and for a borrowed Claude Code
login whose access token has expired that includes a refresh (README, Disclosure). The Hermes import
is lazy: the rest of the plugin, and its tests, run on a bare Python.

Window matching follows the parser this plugin grew out of: case-insensitive substring, "session"
-> the 5-hour window, "week" -> the weekly window, first match wins, except that an account-wide
weekly window beats a model-scoped one (Opus / Sonnet week) when both are present.
"""

from __future__ import annotations

import inspect
import os
import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Callable, Optional

from .pacing import number

PROVIDER = "anthropic"
NO_SNAPSHOT = "no usage: no Claude Pro/Max sign-in in this profile, or the usage endpoint did not answer"


@dataclass(frozen=True)
class Window:
    label: str
    used_pct: Optional[float]
    resets_at: Optional[datetime]
    scope: str = "account"


@dataclass(frozen=True)
class Usage:
    """The windows pacing needs. ``error`` says why usage is unknown (None when a window was read)."""

    five_hour: Optional[Window] = None
    week: Optional[Window] = None
    others: tuple = ()
    source: str = ""
    fetched_at: Optional[datetime] = None
    error: Optional[str] = None
    what_if: bool = False


def aware(value: Any) -> Optional[datetime]:
    """A datetime or ISO string as an aware datetime (no offset = UTC, as Hermes reads it); else None."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _window(raw: Any) -> Window:
    return Window(label=str(getattr(raw, "label", "") or ""), used_pct=number(getattr(raw, "used_percent", None)),
                  resets_at=aware(getattr(raw, "reset_at", None)), scope=str(getattr(raw, "scope", "") or "account"))


def windows_from_snapshot(snapshot: Any) -> Usage:
    """Hermes' ``AccountUsageSnapshot`` (or None) -> ``Usage``. Never raises on odd shapes."""
    if snapshot is None:
        return Usage(error=NO_SNAPSHOT)
    source = " ".join(str(p) for p in (getattr(snapshot, "provider", ""), getattr(snapshot, "source", "")) if p)
    fetched_at = aware(getattr(snapshot, "fetched_at", None))
    reason = getattr(snapshot, "unavailable_reason", None)
    if reason:
        return Usage(source=source, fetched_at=fetched_at, error=str(reason))
    try:
        windows = [_window(w) for w in (getattr(snapshot, "windows", None) or ())]
    except TypeError:
        windows = []
    session = next((w for w in windows if "session" in w.label.lower()), None)
    weeks = [w for w in windows if "week" in w.label.lower() and w is not session]
    week = next((w for w in weeks if w.scope != "model"), weeks[0] if weeks else None)
    others = tuple(w for w in windows if w is not session and w is not week)
    if session is None and week is None:
        return Usage(others=others, source=source, fetched_at=fetched_at,
                     error="the usage snapshot has no 5-hour or weekly window")
    return Usage(five_hour=session, week=week, others=others, source=source, fetched_at=fetched_at)


def _hermes_fetcher() -> Callable[..., Any]:
    from agent.account_usage import fetch_account_usage  # lazy: only inside Hermes

    return fetch_account_usage


def fetch_snapshot(loader: Callable[[], Callable[..., Any]] = _hermes_fetcher) -> Any:
    """One snapshot from Hermes' usage fetcher, called with ``read_only=True`` where the core accepts it
    (older cores get the plain call). The flag only swaps in a fetcher that never repairs a failed read
    by rotating a credential, and current cores have one for Codex alone: for Anthropic it does not stop
    Hermes refreshing an expired Claude Code login while it resolves the sign-in (README, Disclosure)."""
    fetch = loader()
    try:
        read_only = "read_only" in inspect.signature(fetch).parameters
    except (TypeError, ValueError):
        read_only = False
    return fetch(PROVIDER, read_only=True) if read_only else fetch(PROVIDER)


def _home_key() -> str:
    """Cache key: the active profile's home, so a gateway serving several profiles never mixes them."""
    try:
        from hermes_constants import get_hermes_home  # lazy: only inside Hermes

        return str(get_hermes_home())
    except Exception:
        return os.environ.get("HERMES_HOME", "")


_cache: dict[str, tuple[float, Usage]] = {}
_cache_lock = threading.Lock()


def read_usage(fetch: Optional[Callable[[], Any]] = None, cache_seconds: float = 60.0,
               key: Optional[str] = None, clock: Callable[[], float] = time.monotonic) -> Usage:
    """Usage for the active profile, from an in-memory cache younger than ``cache_seconds``
    (0 = always fetch). Fails open: any error becomes ``Usage(error=...)``, never an exception."""
    key = _home_key() if key is None else key
    if cache_seconds > 0:
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None and clock() - hit[0] < cache_seconds:
            return hit[1]
    try:
        usage = windows_from_snapshot((fetch or fetch_snapshot)())
    except Exception as exc:
        usage = Usage(error=f"usage read failed ({type(exc).__name__}: {str(exc)[:120]})")
    if cache_seconds > 0:
        with _cache_lock:
            _cache[key] = (clock(), usage)
    return usage


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _override(name: str, value: Any) -> Optional[float]:
    if value is None:
        return None
    out = number(value)
    if out is None:  # nan / inf compare false against every threshold and would print as NaN in the gate JSON
        raise ValueError(f"what-if {name}: {value!r} is not a finite number")
    return out


def what_if(usage: Usage, h5: Optional[float] = None, week: Optional[float] = None) -> Usage:
    """``usage`` with the used percentages replaced (reset times kept): the CLI's what-if view. A non-finite
    override raises ValueError (the CLI refuses one up front; the surfaces fail open on the error)."""
    if h5 is None and week is None:
        return usage
    h5, week = _override("h5", h5), _override("week", week)
    five_hour, weekly = usage.five_hour, usage.week
    if h5 is not None:
        five_hour = replace(five_hour, used_pct=h5) if five_hour else Window("Current session", h5, None)
    if week is not None:
        weekly = replace(weekly, used_pct=week) if weekly else Window("Current week", week, None)
    return replace(usage, five_hour=five_hour, week=weekly, error=None, what_if=True)
