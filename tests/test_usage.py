"""Reading the windows: snapshot matching, the Hermes fetcher call, the cache and what-if."""
# pyright: reportOptionalMemberAccess=false

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin  # noqa: E402

_plugin.load()
from claude_pacer import usage  # noqa: E402
from claude_pacer.usage import Usage, Window, read_usage, windows_from_snapshot, what_if  # noqa: E402

NOW, hours, window, snapshot = _plugin.NOW, _plugin.hours, _plugin.window, _plugin.snapshot


class TestSnapshotMatching(unittest.TestCase):
    def test_live_shape(self):
        u = windows_from_snapshot(snapshot())
        self.assertEqual((u.five_hour.label, u.five_hour.used_pct, u.five_hour.resets_at), ("Current session", 3.0, hours(4.9)))
        self.assertEqual((u.week.label, u.week.used_pct, u.week.resets_at), ("Current week", 13.0, hours(158.6)))
        self.assertEqual((u.others, u.error, u.source, u.fetched_at), ((), None, "anthropic oauth_usage_api", NOW))

    def test_first_match_wins_and_the_rest_are_listed(self):
        snap = snapshot(extra=(window("Current session (old)", 99, None), window("Sonnet week", 40, hours(100), "model")))
        u = windows_from_snapshot(snap)
        self.assertEqual((u.five_hour.used_pct, u.week.used_pct), (3.0, 13.0))
        self.assertEqual([w.label for w in u.others], ["Current session (old)", "Sonnet week"])

    def test_account_week_beats_a_model_week_listed_first(self):
        snap = snapshot(week=None, extra=(window("Opus week", 70, hours(90), "model"), window("Current week", 20, hours(90))))
        u = windows_from_snapshot(snap)
        self.assertEqual(u.week.label, "Current week")
        self.assertEqual([w.label for w in u.others], ["Opus week"])

    def test_a_model_week_is_used_when_it_is_the_only_one(self):
        u = windows_from_snapshot(snapshot(week=None, extra=(window("Opus week", 70, hours(90), "model"),)))
        self.assertEqual((u.week.label, u.week.used_pct), ("Opus week", 70.0))

    def test_reset_times_and_odd_numbers(self):
        snap = snapshot(h5=None, week=None, extra=(
            window("Current session", "n/a", datetime(2026, 11, 6, 3, 20)),          # naive -> UTC
            window("Current week", True, "2026-11-12T15:00:00+01:00")))              # string, bool
        u = windows_from_snapshot(snap)
        self.assertEqual(u.five_hour.resets_at, datetime(2026, 11, 6, 3, 20, tzinfo=UTC))
        self.assertIsNone(u.five_hour.used_pct)
        self.assertEqual(u.week.resets_at, datetime(2026, 11, 12, 14, 0, tzinfo=UTC))
        self.assertEqual(u.week.resets_at.utcoffset(), timedelta(hours=1))  # the offset is kept
        self.assertIsNone(u.week.used_pct)
        self.assertIsNone(windows_from_snapshot(snapshot(h5_reset_h=None)).five_hour.resets_at)

    def test_unusable_snapshots_say_why(self):
        self.assertEqual(windows_from_snapshot(None).error, usage.NO_SNAPSHOT)
        why = "Anthropic account limits are only available for OAuth-backed Claude accounts."
        u = windows_from_snapshot(snapshot(reason=why))
        self.assertEqual((u.error, u.five_hour, u.week), (why, None, None))
        empty = windows_from_snapshot(snapshot(h5=None, week=None, extra=(window("API key quota", 5),)))
        self.assertEqual(empty.error, "the usage snapshot has no 5-hour or weekly window")
        self.assertEqual(windows_from_snapshot(object()).error, "the usage snapshot has no 5-hour or weekly window")


class TestFetch(unittest.TestCase):
    def test_read_only_where_the_core_supports_it(self):
        calls = []

        def new_core(provider, *, base_url=None, api_key=None, read_only=False, identity_id=None):
            calls.append((provider, read_only))
            return "snap"

        def old_core(provider, *, base_url=None, api_key=None):
            calls.append((provider, "plain"))
            return "snap"
        self.assertEqual(usage.fetch_snapshot(lambda: new_core), "snap")
        self.assertEqual(usage.fetch_snapshot(lambda: old_core), "snap")
        self.assertEqual(calls, [("anthropic", True), ("anthropic", "plain")])

    def test_a_core_without_the_fetcher_fails_open(self):
        def missing():
            raise ImportError("No module named 'agent'")
        u = read_usage(lambda: usage.fetch_snapshot(missing), cache_seconds=0)
        self.assertEqual(u.error, "usage read failed (ImportError: No module named 'agent')")

    def test_fetch_errors_never_raise(self):
        def boom():
            raise RuntimeError("endpoint down")
        u = read_usage(boom, cache_seconds=0, key="t")
        self.assertEqual((u.five_hour, u.week, u.error), (None, None, "usage read failed (RuntimeError: endpoint down)"))


class TestCache(unittest.TestCase):
    def setUp(self):
        usage.clear_cache()
        self.addCleanup(usage.clear_cache)
        self.calls = 0
        self.t = 1000.0

    def fetch(self):
        self.calls += 1
        return snapshot(week=self.calls)

    def read(self, key="home-a", ttl=60):
        return read_usage(self.fetch, cache_seconds=ttl, key=key, clock=lambda: self.t)

    def test_reuses_a_read_inside_the_ttl(self):
        self.assertEqual(self.read().week.used_pct, 1.0)
        self.t += 59
        self.assertEqual(self.read().week.used_pct, 1.0)
        self.t += 2
        self.assertEqual(self.read().week.used_pct, 2.0)
        self.assertEqual(self.calls, 2)

    def test_zero_ttl_always_fetches(self):
        self.read(ttl=0)
        self.read(ttl=0)
        self.assertEqual(self.calls, 2)

    def test_profiles_never_share_an_entry(self):
        self.assertEqual(self.read("home-a").week.used_pct, 1.0)
        self.assertEqual(self.read("home-b").week.used_pct, 2.0)
        self.assertEqual(self.read("home-a").week.used_pct, 1.0)


class TestWhatIf(unittest.TestCase):
    def test_replaces_percentages_and_keeps_resets(self):
        u = what_if(windows_from_snapshot(snapshot()), week=70)
        self.assertEqual((u.week.used_pct, u.week.resets_at, u.five_hour.used_pct, u.what_if), (70, hours(158.6), 3.0, True))

    def test_fills_in_unknown_usage(self):
        u = what_if(Usage(error="no sign-in"), h5=40, week=20)
        self.assertEqual((u.five_hour, u.week, u.error), (Window("Current session", 40, None), Window("Current week", 20, None), None))

    def test_no_overrides_is_a_no_op(self):
        base = windows_from_snapshot(snapshot())
        self.assertIs(what_if(base), base)

    def test_refuses_non_finite_overrides(self):
        base = windows_from_snapshot(snapshot())
        for bad in ({"week": float("nan")}, {"h5": float("inf")}, {"h5": 40.0, "week": float("-inf")}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                what_if(base, **bad)
        with self.assertRaisesRegex(ValueError, r"^what-if week: nan is not a finite number$"):
            what_if(base, week=float("nan"))


if __name__ == "__main__":
    unittest.main()
