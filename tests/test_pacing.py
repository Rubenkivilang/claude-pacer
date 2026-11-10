"""Pacing math: the six cases the plugin exists for, hard stops, no-pace fallbacks and the settings."""
# pyright: reportArgumentType=false

import os
import sys
import unittest
from dataclasses import fields

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin  # noqa: E402

_plugin.load()
from claude_pacer import pacing  # noqa: E402
from claude_pacer.pacing import (DEFAULTS, PRIORITIES, Thresholds, budget, decide, job_runs,  # noqa: E402
                                 limit_spent, week_pace)

NOW, hours, minutes = _plugin.NOW, _plugin.hours, _plugin.minutes


class TestWeekPace(unittest.TestCase):
    def test_line_from_burst_to_target(self):
        self.assertEqual(week_pace(168), 10.0)   # just reset: the burst alone
        self.assertEqual(week_pace(84), 50.0)    # half way: 80 * 0.5 + 10
        self.assertEqual(week_pace(0), 80.0)     # at the reset: the target
        self.assertEqual(week_pace(20), 80.0)    # capped at the target before the end

    def test_out_of_range_reset_times_clamp(self):
        self.assertEqual(week_pace(200), 10.0)   # reset further out than a week
        self.assertEqual(week_pace(-3), 80.0)    # reset already passed (stale snapshot)

    def test_follows_settings(self):
        self.assertEqual(week_pace(84, Thresholds(reserve_pct=40, pace_burst_pct=0)), 30.0)  # target 60


class TestRequiredCases(unittest.TestCase):
    """The acceptance cases: burst, light, skip, heavy, fresh 5h window, fail open."""

    def test_early_week_burst_reads_on_pace(self):
        d = decide(20, 12, hours(160), NOW, h5_reset=hours(3))
        self.assertEqual((d.mode, d.rule, d.week_pace_pct), ("normal", "on_pace", 13.8))
        self.assertEqual(d.reason, "week 12% vs pace 13.8%, resets in 160.0h")
        # Without the head start the same burst would already read as over pace.
        no_burst = decide(20, 12, hours(160), NOW, h5_reset=hours(3), t=Thresholds(pace_burst_pct=0))
        self.assertEqual((no_burst.mode, no_burst.rule, no_burst.week_pace_pct), ("light", "over_pace", 3.8))

    def test_over_pace_is_light(self):
        d = decide(10, 55, hours(84), NOW, h5_reset=hours(2))
        self.assertEqual((d.mode, d.rule), ("light", "over_pace"))
        self.assertEqual(d.reason, "over pace; week 55% vs pace 50%, resets in 84.0h")
        self.assertEqual((d.week_resets_in_h, d.week_pace_pct), (84.0, 50.0))

    def test_far_over_pace_is_skip(self):
        d = decide(10, 66, hours(84), NOW, h5_reset=hours(2))
        self.assertEqual((d.mode, d.rule), ("skip", "far_over_pace"))
        self.assertEqual(d.reason, "over pace + 15; week 66% vs pace 50%, resets in 84.0h")
        self.assertEqual(decide(10, 65, hours(84), NOW).mode, "light")  # pace + 15 exactly is not over

    def test_under_pace_near_reset_is_heavy(self):
        d = decide(10, 40, hours(20), NOW, h5_reset=hours(2))
        self.assertEqual((d.mode, d.rule), ("heavy", "reset_soon_spare"))
        self.assertEqual(d.reason, "week 40% vs pace 80%, resets in 20.0h")
        self.assertEqual(decide(10, 60, hours(20), NOW).mode, "normal")  # over the use-it-or-lose-it ceiling

    def test_well_under_pace_is_heavy(self):
        d = decide(10, 14, hours(84), NOW)  # mid-week the heavy line is at 14.6 %
        self.assertEqual((d.mode, d.rule), ("heavy", "under_pace"))
        self.assertEqual(decide(10, 15, hours(84), NOW).mode, "normal")
        flat = Thresholds(heavy_ramp=0)  # the straight rule: pace - 25
        self.assertEqual(decide(10, 20, hours(84), NOW, t=flat).rule, "under_pace")
        self.assertEqual(decide(10, 25, hours(84), NOW, t=flat).mode, "normal")  # pace - 25 exactly is not under

    def test_heavy_edge_is_one_smooth_curve(self):
        """0.4.1 (Ruben, Nov 10: "a weird corner"): with the defaults, the top of the heavy zone climbs smoothly
        all week. The use-it-or-lose-it ceiling (50 %) sits under the heavy line's 50.7 % at 24 h before the
        reset, so it never shows: no jump at 144 h, no flat shelf after it (0.4.0's ceiling 55 made both)."""

        def edge(resets_in_h):  # the highest week % decide() still calls heavy (bisection; heavy is 0 % up to it)
            lo, hi = 0.0, 100.0
            self.assertEqual(decide(10, lo, hours(resets_in_h), NOW).mode, "heavy")
            for _ in range(40):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if decide(10, mid, hours(resets_in_h), NOW).mode == "heavy" else (lo, mid)
            return lo

        elapsed = [i / 2 for i in range(2, 168 * 2)]  # every 30 min, 1 h after the reset (line 0.1 %) to 30 min before
        edges = [edge(168 - e) for e in elapsed]
        # the edge IS the heavy line, all week: one curve (0.4.0 sat at its 55 % ceiling from 144 h to ~148.5 h)
        for e, v in zip(elapsed, edges):
            self.assertAlmostEqual(v, pacing.heavy_line(168 - e, DEFAULTS), delta=0.001, msg=f"{e} h after the reset")
        steps = [b - a for a, b in zip(edges, edges[1:])]
        self.assertTrue(all(s >= 0 for s in steps), "the heavy edge never drops")
        self.assertLess(max(steps), 1.3)  # no jump: the largest step is the line's own in the last 30 min (0.4.0: 4.3)
        late = steps[elapsed.index(132):]  # no flat shelf in the last 36 h: the line climbs > 0.1 per 30 min there
        self.assertTrue(all(s > 0 for s in late), "the heavy edge must keep climbing to the reset")
        i = elapsed.index(144)
        self.assertLess(edges[i] - edges[i - 1], 0.6)  # the line's own 0.5 per 30 min there (0.4.0: 50.2 -> 55)
        self.assertGreater(pacing.heavy_line(24, DEFAULTS), DEFAULTS.heavy_reset_week_max)  # 50.7 > 50 at 144 h
        self.assertEqual(decide(10, 50.3, hours(24), NOW).rule, "under_pace")  # over the ceiling, under the line
        self.assertEqual(decide(10, 50, hours(20), NOW).rule, "reset_soon_spare")  # the ceiling still applies

    def test_heavy_needs_a_quiet_5h_window(self):
        self.assertEqual(decide(55, 10, hours(84), NOW, h5_reset=hours(3)).mode, "normal")
        self.assertEqual(decide(None, 10, hours(84), NOW).mode, "normal")  # unknown 5h: never heavy

    def test_5h_window_resetting_within_45_min_counts_fresh(self):
        d = decide(70, 10, hours(84), NOW, h5_reset=minutes(20))  # 70 + 0.5 * 20 = 80 < 85
        self.assertEqual((d.mode, d.rule, d.h5_counted_fresh), ("heavy", "under_pace", True))
        self.assertEqual(d.reason, "5h 70% resets in 20 min, counted fresh; week 10% vs pace 50%, resets in 84.0h")
        # The same 70 % with the reset 50 min away is just a hot window.
        late = decide(70, 10, hours(84), NOW, h5_reset=minutes(50))
        self.assertEqual((late.mode, late.rule, late.h5_counted_fresh), ("light", "h5_hot", False))
        self.assertEqual(late.reason, "5h 70% >= 60%; week 10% vs pace 50%, resets in 84.0h")

    def test_fresh_needs_the_burn_to_stay_under_the_hard_stop(self):
        d = decide(80, 20, hours(84), NOW, h5_reset=minutes(20))  # 80 + 10 = 90 >= 85
        self.assertEqual((d.mode, d.rule, d.h5_counted_fresh), ("light", "h5_hot", False))

    def test_fresh_boundary_and_quiet_note(self):
        self.assertTrue(decide(60, 20, hours(84), NOW, h5_reset=minutes(45)).h5_counted_fresh)
        self.assertFalse(decide(60, 20, hours(84), NOW, h5_reset=minutes(46)).h5_counted_fresh)
        quiet = decide(30, 20, hours(84), NOW, h5_reset=minutes(10))  # fresh, but under 50: no note
        self.assertTrue(quiet.h5_counted_fresh)
        self.assertEqual(quiet.reason, "week 20% vs pace 50%, resets in 84.0h")

    def test_missing_usage_fails_open_to_normal(self):
        for week_reset in (None, hours(84)):
            d = decide(None, None, week_reset, NOW)
            self.assertEqual((d.mode, d.rule, d.reason), ("normal", "usage_unknown", "usage unknown, fail open"))
            self.assertIsNone(d.week_pace_pct)


class TestHardStopsAndFallbacks(unittest.TestCase):
    def test_hard_stops_read_the_raw_numbers(self):
        self.assertEqual(budget(85, 10, hours(84), NOW), ("skip", "5h 85% >= 85% hard stop", None, None))
        self.assertEqual(budget(90, 10, hours(84), NOW, h5_reset=minutes(5))[0], "skip")  # fresh can't save it
        self.assertEqual(budget(10, 80, hours(84), NOW), ("skip", "week 80% >= 80% hard stop", None, None))
        self.assertEqual(decide(10, 79.5, hours(84), NOW).rule, "far_over_pace")

    def test_unknown_reset_time_means_no_pacing(self):
        self.assertEqual(budget(10, 65, None, NOW), ("light", "week 65% >= 60%; week reset time unknown", None, None))
        self.assertEqual(budget(65, 10, None, NOW), ("light", "5h 65% >= 60%; week reset time unknown", None, None))
        self.assertEqual(budget(10, 30, None, NOW), ("normal", "week reset time unknown, no pacing", None, None))
        self.assertEqual(budget(0, 0, None, NOW)[0], "normal")  # never heavy without a pace line

    def test_unknown_week_with_known_5h(self):
        d = decide(10, None, hours(84), NOW)
        self.assertEqual((d.mode, d.reason), ("normal", "week unknown vs pace 50%, resets in 84.0h"))

    def test_non_numeric_input_raises(self):
        with self.assertRaises(TypeError):
            decide("x", None, None, NOW)


class TestSettings(unittest.TestCase):
    def test_defaults_are_the_gate_values(self):
        self.assertEqual(DEFAULTS, Thresholds(85, 20, 10, 15, 45, 0.5, 60, 60, 50, 24, 30, 25, 3))
        # The wake gate's WEEK_TARGET_PCT, WEEK_SKIP_PCT and HEAVY_RESET_WEEK_MAX, all from the one reserve.
        self.assertEqual((DEFAULTS.week_target_pct, DEFAULTS.week_skip_pct, DEFAULTS.heavy_reset_week_max),
                         (80, 80, 50))
        t, warnings = pacing.thresholds_from(_plugin.config())
        self.assertEqual((t, warnings), (DEFAULTS, []))

    def test_settings_change_the_rules(self):
        t, warnings = pacing.thresholds_from(_plugin.config(reserve_pct=40, h5_skip_pct=70, pace_skip_margin=5))
        self.assertEqual(warnings, [])
        self.assertEqual(decide(10, 45, hours(84), NOW, t=t).reason, "over pace; week 45% vs pace 40%, resets in 84.0h")
        self.assertEqual(decide(75, 10, hours(84), NOW, t=t).reason, "5h 75% >= 70% hard stop")
        self.assertEqual(decide(10, 46, hours(84), NOW, t=t).reason, "over pace + 5; week 46% vs pace 40%, resets in 84.0h")

    def test_bad_values_fall_back_to_defaults_with_a_warning(self):
        t, warnings = pacing.thresholds_from(_plugin.config(
            reserve_pct="abc", h5_skip_pct=150, h5_light_pct=True, pace_burst_pct=float("nan"),
            h5_heavy_max="40"))
        self.assertEqual((t.reserve_pct, t.h5_skip_pct, t.h5_light_pct, t.pace_burst_pct), (20, 85, 60, 10))
        self.assertEqual(t.h5_heavy_max, 40.0)  # a numeric string is fine
        self.assertEqual(len(warnings), 4)
        self.assertIn("reserve_pct: 'abc' is not a number in 0..100, using 20", warnings)

    def test_a_failing_config_read_never_breaks_pacing(self):
        def broken(key, default=None):
            raise OSError("config.yaml unreadable")
        t, warnings = pacing.thresholds_from(broken)
        self.assertEqual(t, DEFAULTS)
        self.assertEqual(len(warnings), len(pacing.RANGES))
        self.assertIn("could not be read (OSError)", warnings[0])

    def test_every_setting_has_a_range(self):
        self.assertEqual(set(pacing.RANGES), {f for f in Thresholds.__dataclass_fields__})


class TestReserve(unittest.TestCase):
    """reserve_pct, the share of the week the user keeps: one value moves the pace line's target, the weekly
    hard stop and the use-it-or-lose-it ceiling together."""

    def test_reserve_sets_the_target_and_the_hard_stop(self):
        t = Thresholds(reserve_pct=30)
        self.assertEqual((t.week_target_pct, t.week_skip_pct, t.heavy_reset_week_max), (70, 70, 40))
        self.assertEqual((week_pace(168, t), week_pace(84, t), week_pace(0, t)), (10.0, 45.0, 70.0))
        d = decide(10, 70, hours(84), NOW, t=t)
        self.assertEqual((d.mode, d.rule, d.reason), ("skip", "week_hard_stop", "week 70% >= 70% hard stop"))
        self.assertEqual(decide(10, 69.5, hours(10), NOW, t=t).mode, "normal")  # just under the line near the reset

    def test_a_bigger_reserve_reads_the_same_week_hotter(self):
        """The demo's Sunday 15:00: 42 % used with 96 h to go is on pace with a 20 % reserve, over pace with 30 %."""
        default = decide(12, 42, hours(96), NOW, h5_reset=hours(3))
        self.assertEqual((default.mode, default.rule, default.week_pace_pct), ("normal", "on_pace", 44.3))
        kept = decide(12, 42, hours(96), NOW, h5_reset=hours(3), t=Thresholds(reserve_pct=30))
        self.assertEqual((kept.mode, kept.rule, kept.reason),
                         ("light", "over_pace", "over pace; week 42% vs pace 40%, resets in 96.0h"))

    def test_use_it_or_lose_it_leaves_the_reserve_alone(self):
        t = Thresholds(reserve_pct=40, heavy_ramp=0)  # target 60, straight rule: heavy before the reset only <= 30
        self.assertEqual(decide(10, 40, hours(20), NOW).rule, "reset_soon_spare")  # the default reserve: heavy
        self.assertEqual(decide(10, 40, hours(20), NOW, t=t).mode, "normal")
        self.assertEqual(decide(10, 30, hours(20), NOW, t=t).rule, "reset_soon_spare")
        # the exponential heavy line reaches further late in the week, but heavy never touches the target:
        # the reserve stays the hard stop
        b = Thresholds(reserve_pct=40)
        for h in (20, 5, 1, 0.1):
            self.assertEqual(decide(10, 60, hours(h), NOW, t=b).rule, "week_hard_stop")
            self.assertLess(pacing.heavy_line(h, b), b.week_target_pct)

    def test_no_reserve_and_a_full_reserve(self):
        none, full = Thresholds(reserve_pct=0), Thresholds(reserve_pct=100)
        self.assertEqual(decide(10, 99, hours(1), NOW, t=none).mode, "normal")  # paced all the way to 100 %
        self.assertEqual(decide(10, 100, hours(1), NOW, t=none).rule, "week_hard_stop")
        self.assertEqual(decide(10, 0, hours(84), NOW, t=full).rule, "week_hard_stop")  # agents never run

    def test_reserve_status(self):
        rs = pacing.reserve_status
        self.assertEqual(rs(13, 14.5), "on track")
        self.assertEqual(rs(14.5, 14.5), "on track")  # on the line is on track
        self.assertEqual(rs(55, 50), "at risk")
        self.assertEqual(rs(80, 80), "in use")        # target reached: agents stop, the rest is the reserve
        self.assertEqual(rs(95, None), "in use")
        self.assertIsNone(rs(30, None))               # no pace line to compare with
        self.assertIsNone(rs(None, 40))
        self.assertEqual(rs(72, 70, Thresholds(reserve_pct=30)), "in use")
        self.assertEqual(rs(72, 70), "at risk")

    def test_a_bad_reserve_falls_back_to_twenty(self):
        for bad in (-5, 101, "lots", float("inf"), True):
            t, warnings = pacing.thresholds_from(_plugin.config(reserve_pct=bad))
            self.assertEqual((t.reserve_pct, len(warnings)), (20, 1), bad)


class TestJobPriorities(unittest.TestCase):
    """Gated cron jobs: vital > normal > optional > spare. Each priority waits in fewer modes than the next."""

    def test_which_priority_runs_in_which_mode(self):
        runs = {m: tuple(p for p in PRIORITIES if job_runs(p, m, h5=10, week=50)) for m in pacing.MODES}
        self.assertEqual(runs, {"skip": ("vital",), "light": ("vital", "normal"),
                                "normal": ("vital", "normal", "optional"),
                                "heavy": ("vital", "normal", "optional", "spare")})

    def test_the_default_priority_is_the_old_default_gate(self):
        """A job gated without --priority behaves like the gate did before priorities: waits only in skip."""
        self.assertEqual(pacing.DEFAULT_PRIORITY, "normal")
        self.assertEqual([m for m in pacing.MODES if not job_runs("normal", m)], ["skip"])
        self.assertEqual([m for m in pacing.MODES if job_runs("bogus", m)], ["light", "normal", "heavy"])

    def test_vital_runs_in_every_mode_until_a_limit_is_used_up(self):
        """Pacing never stops a vital job (Ruben, Nov 8): only a window at 100 %, where the API refuses anyway."""
        for h5, week, expected in ((10, 50, True), (85, 50, True), (95, 50, True), (99.9, 50, True),
                                   (100, 50, False), (10, 80, True), (10, 98, True), (10, 99.9, True),
                                   (10, 100, False), (None, None, True), (None, 100, False), (100, None, False),
                                   (10, 101, False)):
            for mode in pacing.MODES:
                self.assertEqual(job_runs("vital", mode, h5=h5, week=week), expected, (mode, h5, week))
        self.assertEqual(limit_spent(100, 50), "5h 100%: the 5-hour limit is used up")
        self.assertEqual(limit_spent(10, 100), "week 100%: the weekly limit is used up")
        self.assertIsNone(limit_spent(99.9, 99.9))
        self.assertIsNone(limit_spent(None, None))

    def test_the_old_vital_stops_are_retired(self):
        """0.3.0's vital_h5_stop_pct / vital_week_stop_pct no longer exist; a config that still sets them gets a
        warning and no stop."""
        self.assertFalse({"vital_h5_stop_pct", "vital_week_stop_pct"} & {f.name for f in fields(Thresholds)})
        self.assertFalse({"vital_h5_stop_pct", "vital_week_stop_pct"} & set(pacing.RANGES))
        t, warnings = pacing.thresholds_from(_plugin.config(vital_h5_stop_pct=90, vital_week_stop_pct=85))
        self.assertEqual(t, pacing.DEFAULTS)
        self.assertEqual(warnings, [f"{n}: no longer used, vital jobs stop only when a limit is used up (100%)"
                                    for n in ("vital_h5_stop_pct", "vital_week_stop_pct")])
        self.assertTrue(job_runs("vital", "skip", h5=91, week=86, t=t))
        self.assertEqual(pacing.thresholds_from(_plugin.config())[1], [])  # nothing set, nothing said

    def test_a_more_important_job_runs_whenever_a_less_important_one_does(self):
        """Monotone on a grid of real decisions: if spare runs, optional does; if optional, normal; if normal, vital."""
        for h5 in (0, 30, 55, 65, 86, 96, 100):
            for week in (0, 20, 45, 60, 79, 80, 90, 99, 100):
                for left in (1, 20, 84, 160):
                    d = decide(h5, week, hours(left), NOW)
                    ran = [job_runs(p, d.mode, h5, week) for p in PRIORITIES]
                    self.assertEqual(ran, sorted(ran, reverse=True), (h5, week, left, d.mode, ran))

    def test_a_vital_job_runs_into_the_reserve_and_a_normal_one_stops_at_it(self):
        d = decide(10, 85, hours(30), NOW)
        self.assertEqual((d.mode, d.rule), ("skip", "week_hard_stop"))
        self.assertEqual((job_runs("normal", d.mode, 10, 85), job_runs("vital", d.mode, 10, 85)), (False, True))
        for reserve in (10, 20, 50):  # whatever the reserve, deep into it
            t = Thresholds(reserve_pct=reserve)
            d = decide(10, 99.5, hours(30), NOW, t=t)
            self.assertEqual(d.mode, "skip")
            self.assertEqual([p for p in PRIORITIES if job_runs(p, d.mode, 10, 99.5, t)], ["vital"], reserve)


if __name__ == "__main__":
    unittest.main()


class TestBackLoadedHeavy(unittest.TestCase):
    """heavy_line: heavy work is back-loaded, its room grows exponentially toward the reset."""

    def test_heavy_line_is_exponential(self):
        hl = pacing.heavy_line
        self.assertEqual([hl(h) for h in (168, 84, 42, 16.8, 0)], [0.0, 14.6, 35.6, 58.2, 80.0])
        line = [hl(168 - h) for h in range(0, 169, 12)]
        steps = [b - a for a, b in zip(line, line[1:])]
        self.assertTrue(all(b > a for a, b in zip(steps, steps[1:])), steps)  # every day it climbs faster
        self.assertGreater(steps[-1], 10 * steps[0])

    def test_heavy_runs_late_in_the_week(self):
        # the same 30 % week: normal mid-week, heavy two days before the reset
        self.assertEqual(decide(5, 30, hours(84), NOW).mode, "normal")
        self.assertEqual((decide(5, 30, hours(48), NOW).mode, decide(5, 30, hours(48), NOW).rule),
                         ("heavy", "under_pace"))

    def test_ramp_zero_is_the_straight_rule(self):
        flat = Thresholds(heavy_ramp=0)
        for h in (160, 84, 30, 2):
            self.assertEqual(pacing.heavy_line(h, flat), round(pacing.week_pace(h, flat) - 25, 1))

    def test_ramp_is_a_setting(self):
        t, warnings = pacing.thresholds_from(_plugin.config(heavy_ramp=6))
        self.assertEqual((t.heavy_ramp, warnings), (6, []))
        self.assertLess(pacing.heavy_line(42, t), pacing.heavy_line(42))  # steeper: later
        t, warnings = pacing.thresholds_from(_plugin.config(heavy_ramp=11))
        self.assertEqual(t.heavy_ramp, 3)
        self.assertTrue(warnings and "heavy_ramp" in warnings[0], warnings)
