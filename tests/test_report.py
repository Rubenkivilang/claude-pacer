"""The outputs: tool payload, the one human line with its bar, and the cron gate contract."""
# pyright: reportArgumentType=false

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin  # noqa: E402

_plugin.load()
from claude_pacer import report  # noqa: E402
from claude_pacer.pacing import DEFAULTS, Thresholds  # noqa: E402
from claude_pacer.report import assess, bar, fmt_hours, gate_line, gate_result, human_line  # noqa: E402
from claude_pacer.usage import Usage, Window, windows_from_snapshot  # noqa: E402

NOW, hours, window, snapshot = _plugin.NOW, _plugin.hours, _plugin.window, _plugin.snapshot


def pay(**snap):
    return assess(windows_from_snapshot(snapshot(**snap)), NOW)


class TestBar(unittest.TestCase):
    def test_cells(self):
        self.assertEqual(bar(13, 14.5), "███" + "░" * 17)                   # on pace
        self.assertEqual(bar(20, 50), "█" * 4 + "▒" * 6 + "░" * 10)          # headroom up to the pace line
        self.assertEqual(bar(55, 50), "█" * 10 + "▓" + "░" * 9)              # use beyond pace
        self.assertEqual(bar(100, 80), "█" * 16 + "▓" * 4)
        self.assertEqual(len(bar(0, 0)), report.BAR_WIDTH)
        self.assertEqual(bar(None, 50), "")


class TestDurations(unittest.TestCase):
    def test_fmt_hours(self):
        cases = {158.6: "6d 15h", 84.0: "3d 12h", 48.0: "2d", 47.99: "47h 59m", 24.0: "24h", 4.9: "4h 54m",
                 2.0: "2h", 0.33: "20 min", 0.0: "now", -1.5: "now", None: "?"}
        for hours_left, text in cases.items():
            self.assertEqual(fmt_hours(hours_left), text, hours_left)


class TestPayload(unittest.TestCase):
    def test_live_shape(self):
        p = pay()
        self.assertEqual((p["mode"], p["rule"], p["reason"]), ("normal", "on_pace", "week 13% vs pace 14.5%, resets in 158.6h"))
        self.assertEqual(p["guidance"], report.GUIDANCE["normal"])
        self.assertEqual(p["five_hour"], {"used_pct": 3.0, "resets_at": hours(4.9).isoformat(), "resets_in_h": 4.9,
                                          "counted_fresh": False})
        self.assertEqual(p["week"], {"used_pct": 13.0, "resets_at": hours(158.6).isoformat(), "resets_in_h": 158.6,
                                     "pace_pct": 14.5, "reserve_pct": 20, "reserve_status": "on track"})
        self.assertEqual((p["source"], p["fetched_at"]), ("Hermes account usage (anthropic oauth_usage_api)", NOW.isoformat()))
        self.assertEqual(p["cron_jobs"], {"vital": "runs", "normal": "runs", "optional": "runs", "spare": "waits"})
        self.assertNotIn("usage_error", p)
        json.dumps(p)  # the tool returns it as JSON

    def test_hard_stop_still_reports_pace_and_hours_to_reset(self):
        p = pay(h5=90, week=10, h5_reset_h=2, week_reset_h=84)
        self.assertEqual((p["mode"], p["rule"]), ("skip", "h5_hard_stop"))
        self.assertEqual((p["week"]["resets_in_h"], p["week"]["pace_pct"], p["five_hour"]["resets_in_h"]), (84.0, 50.0, 2.0))
        self.assertEqual(p["week"]["reserve_status"], "on track")  # the 5h stop says nothing about the week

    def test_unknown_usage_fails_open(self):
        p = assess(Usage(error="no usage returned"), NOW)
        self.assertEqual((p["mode"], p["rule"], p["usage_error"]), ("normal", "usage_unknown", "no usage returned"))
        self.assertEqual(p["week"], {"used_pct": None, "resets_at": None, "resets_in_h": None, "pace_pct": None,
                                     "reserve_pct": 20, "reserve_status": None})

    def test_bad_numbers_fail_open(self):
        p = assess(Usage(five_hour=Window("Current session", "x", None)), NOW)
        self.assertEqual((p["mode"], p["rule"], p["reason"]), ("normal", "error", "budget error (TypeError), fail open"))
        self.assertEqual(p["summary"], "🟢 normal · pacing error, fail open · crons run: vital, normal, optional")

    def test_extras(self):
        p = assess(windows_from_snapshot(snapshot(extra=(window("Opus week", 40, hours(100), "model"),))), NOW,
                   DEFAULTS, ["h5_skip_pct: 'x' is not a number in 0..100, using 85"])
        self.assertEqual(p["other_windows"], [{"label": "Opus week", "used_pct": 40.0, "resets_at": hours(100).isoformat()}])
        self.assertEqual(p["config_warnings"], ["h5_skip_pct: 'x' is not a number in 0..100, using 85"])


class TestHumanLine(unittest.TestCase):
    def test_live_shape(self):
        self.assertEqual(pay()["summary"],
                         "🟢 normal · on pace · week 13% ███░░░░░░░░░░░░░░░░░ pace 14.5% · reserve 20%: on track · "
                         "resets in 6d 15h · 5h 3% (resets in 4h 54m) · crons run: vital, normal, optional")

    def test_fresh_window(self):
        line = pay(h5=70, h5_reset_h=20 / 60, week=10, week_reset_h=84)["summary"]  # under the 14.6 % heavy line
        self.assertEqual(line, "🔵 heavy · well under pace · week 10% ██▒▒▒▒▒▒▒▒░░░░░░░░░░ pace 50% · reserve 20%: on track · "
                               "resets in 3d 12h · 5h 70% (resets in 20 min, counted fresh) · crons run: all")

    def test_says_which_cron_priorities_run(self):
        """/pace ends with the gated cron priorities that run right now (which ones wait follows from it)."""
        for snap, tail in (({}, "crons run: vital, normal, optional"),
                           ({"week": 55, "week_reset_h": 84}, "crons run: vital, normal"),
                           ({"week": 66, "week_reset_h": 84}, "crons run: vital only, lightly"),
                           ({"h5": 96}, "crons run: vital only, lightly"),  # pacing never stops vital
                           ({"h5": 100}, "crons run: none"),                # a used-up limit does
                           ({"week": 40, "week_reset_h": 20}, "crons run: all")):
            self.assertTrue(pay(**snap)["summary"].endswith(" · " + tail), (snap, pay(**snap)["summary"]))
        self.assertEqual(report.crons_text(None), "")

    def test_reserve_says_how_it_stands(self):
        """`/pace` names the reserve and its status ("reserve 20%: on track") wherever the week is known."""
        self.assertIn(" · reserve 20%: at risk · ", pay(week=55, week_reset_h=84)["summary"])
        hard = pay(week=85, week_reset_h=84)["summary"]
        self.assertTrue(hard.startswith("🔴 skip · reserve reached · week 85% "), hard)
        self.assertIn(" · reserve 20%: in use · ", hard)
        self.assertTrue(hard.endswith(" · crons run: vital only, lightly"), hard)  # vital goes on, into the reserve
        thirty = assess(windows_from_snapshot(snapshot(week=42, week_reset_h=96)), NOW, Thresholds(reserve_pct=30))
        self.assertEqual(thirty["summary"], "🟡 light · over pace · week 42% ████████░░░░░░░░░░░░ pace 40% · "
                                            "reserve 30%: at risk · resets in 4d · 5h 3% (resets in 4h 54m) · "
                                            "crons run: vital, normal")
        no_pace = pay(week_reset_h=None)["summary"]  # no reset time, no pace line: the reserve without a status
        self.assertIn("week 13% · reserve 20% · 5h 3%", no_pace)
        self.assertNotIn("reserve", pay(week=None)["summary"])  # the week unknown: nothing to say about it

    def test_every_mode_is_one_line(self):
        for snap in ({}, {"h5": 90}, {"week": 66, "week_reset_h": 84}, {"week": 55, "week_reset_h": 84},
                     {"week": 40, "week_reset_h": 20}, {"week_reset_h": None}, {"h5": None, "week": None}):
            line = pay(**snap)["summary"]
            self.assertNotIn("\n", line)
            self.assertTrue(line.split(" ")[1] in ("skip", "light", "normal", "heavy"), line)

    def test_unknown_usage_says_why(self):
        line = human_line(assess(Usage(error="no usage returned: no sign-in"), NOW))
        self.assertEqual(line, "🟢 normal · usage unknown, fail open: no usage returned: no sign-in · "
                               "crons run: vital, normal, optional")
        long = human_line(assess(Usage(error="word " * 40), NOW))  # cut on a word, never mid-word
        self.assertTrue(long.endswith("word … · crons run: vital, normal, optional"), long)
        self.assertLess(len(long), 210)

    def test_what_if_is_labelled(self):
        payload = assess(Usage(week=Window("Current week", 70, hours(84)), what_if=True), NOW)
        self.assertTrue(payload["summary"].startswith("🔴 skip · far over pace (what-if) · week 70%"))


class TestGate(unittest.TestCase):
    CONTEXT_KEYS = {"budget_mode", "budget_reason", "budget_guidance", "five_hour_used_pct", "week_used_pct",
                    "week_resets_in_h", "week_pace_pct"}

    def test_a_waiting_job_gets_wake_agent_false_with_its_priority_and_why(self):
        for snap, rule in (({"h5": 90}, "5h hard stop reached"), ({"week": 80}, "reserve reached"),
                           ({"week": 66, "week_reset_h": 84}, "far over pace")):
            line = gate_line(pay(**snap))
            self.assertNotIn("\n", line)
            self.assertEqual(json.loads(line), {
                "wakeAgent": False, "priority": "normal",
                "why": f"normal job, skip mode ({rule}): waits; only vital jobs run in skip mode"})

    def test_wake_carries_the_budget(self):
        doc = gate_result(pay())
        self.assertEqual((doc["wakeAgent"], doc["priority"], doc["why"]),
                         (True, "normal", "normal job, normal mode (on pace): runs"))
        self.assertEqual(set(doc), {"wakeAgent", "priority", "why", "context"})
        self.assertEqual(set(doc["context"]), self.CONTEXT_KEYS)
        self.assertEqual((doc["context"]["budget_mode"], doc["context"]["week_pace_pct"], doc["context"]["week_resets_in_h"],
                          doc["context"]["five_hour_used_pct"]), ("normal", 14.5, 158.6, 3.0))
        line = gate_line(pay())
        self.assertNotIn("\n", line)
        self.assertEqual(json.loads(line), doc)

    def test_unknown_usage_wakes(self):
        doc = gate_result(assess(Usage(error="x"), NOW))
        self.assertEqual((doc["wakeAgent"], doc["context"]["budget_mode"], doc["context"]["budget_reason"]),
                         (True, "normal", "usage unknown, fail open"))
        self.assertIs(gate_result(assess(Usage(error="x"), NOW), "spare")["wakeAgent"], False)

    def test_priorities(self):
        skip, light, normal, heavy = (pay(week=66, week_reset_h=84), pay(week=55, week_reset_h=84), pay(),
                                      pay(week=40, week_reset_h=20))
        self.assertEqual([p["mode"] for p in (skip, light, normal, heavy)], ["skip", "light", "normal", "heavy"])
        runs = {p["mode"]: [q for q in ("vital", "normal", "optional", "spare") if gate_result(p, q)["wakeAgent"]]
                for p in (skip, light, normal, heavy)}
        self.assertEqual(runs, {"skip": ["vital"], "light": ["vital", "normal"], "normal": ["vital", "normal", "optional"],
                                "heavy": ["vital", "normal", "optional", "spare"]})
        self.assertEqual(gate_result(light, "optional")["why"],
                         "optional job, light mode (over pace): waits; optional jobs run again once the week is back on pace")
        self.assertEqual(gate_result(normal, "spare")["why"],
                         "spare job, normal mode (on pace): waits; spare jobs run only in heavy mode")
        self.assertEqual(gate_result(heavy, "spare")["why"], "spare job, heavy mode (spare capacity, week resets soon): runs")
        self.assertEqual(gate_result(normal, "nonsense")["priority"], "normal")  # an unknown priority is the default

    def test_a_vital_job_runs_lightly_in_skip_mode(self):
        doc = gate_result(pay(week=66, week_reset_h=84), "vital")
        self.assertEqual((doc["wakeAgent"], doc["priority"], doc["context"]["budget_mode"]), (True, "vital", "skip"))
        self.assertEqual(doc["context"]["budget_guidance"], report.VITAL_SKIP_GUIDANCE)
        self.assertEqual(doc["why"], "vital job, skip mode (far over pace): runs lightly; vital jobs may use the "
                                     "reserve, only a used-up limit stops them")
        self.assertIn("reserve", report.VITAL_SKIP_GUIDANCE)
        self.assertIn("essential part", report.VITAL_SKIP_GUIDANCE)
        self.assertEqual(gate_result(pay(week=55, week_reset_h=84), "vital")["context"]["budget_guidance"],
                         report.GUIDANCE["light"])  # outside skip a vital job gets the mode's own guidance

    def test_a_vital_job_runs_into_the_reserve_up_to_a_used_up_limit(self):
        """Pacing never stops a vital job: past the 5h hard stop, in the reserve, at 99.9 % it still runs, with the
        light guidance. Only a window at 100 % (the API refuses) makes it wait."""
        for snap in ({"h5": 96}, {"h5": 99.9}, {"week": 85, "week_reset_h": 30}, {"week": 99.9, "week_reset_h": 30},
                     {"h5": 99, "week": 99, "week_reset_h": 2}):
            doc = gate_result(pay(**snap), "vital")
            self.assertEqual((doc["wakeAgent"], doc["context"]["budget_mode"], doc["context"]["budget_guidance"]),
                             (True, "skip", report.VITAL_SKIP_GUIDANCE), snap)
            self.assertIs(gate_result(pay(**snap), "normal")["wakeAgent"], False, snap)  # normal unchanged: waits
        reserve = gate_result(pay(week=85, week_reset_h=30), "vital")
        self.assertEqual(reserve["why"], "vital job, skip mode (reserve reached): runs lightly; vital jobs may use the "
                                         "reserve, only a used-up limit stops them")
        self.assertEqual(gate_result(pay(h5=100), "vital"), {"wakeAgent": False, "priority": "vital", "why": (
            "vital job, skip mode (5h hard stop reached): waits, 5h 100%: the 5-hour limit is used up; vital jobs "
            "run again once it resets")})
        self.assertEqual(gate_result(pay(week=100, week_reset_h=30), "vital")["why"],
                         "vital job, skip mode (reserve reached): waits, week 100%: the weekly limit is used up; vital "
                         "jobs run again once it resets")
        for t in (Thresholds(), Thresholds(reserve_pct=0), Thresholds(reserve_pct=50, h5_skip_pct=99)):
            self.assertIs(gate_result(pay(h5=99.5), "vital", t)["wakeAgent"], True, t)  # no setting moves the stop

    def test_fail_open_line(self):
        doc = json.loads(report.fail_open_gate())
        self.assertEqual((doc["wakeAgent"], doc["priority"], doc["context"]["budget_mode"]), (True, "normal", "normal"))
        self.assertEqual(json.loads(report.fail_open_gate("spare"))["wakeAgent"], False)
        self.assertEqual(json.loads(report.fail_open_gate("vital"))["priority"], "vital")

    def test_what_if_numbers_are_flagged_to_the_agent(self):
        doc = gate_result(assess(Usage(week=Window("Current week", 20, hours(84)), what_if=True), NOW))
        self.assertEqual((doc["wakeAgent"], doc["context"]["what_if"]), (True, True))
        self.assertNotIn("what_if", gate_result(pay())["context"])
        waits = gate_result(assess(Usage(week=Window("Current week", 90, hours(84)), what_if=True), NOW))
        self.assertEqual((waits["wakeAgent"], waits["what_if"]), (False, True))
        self.assertIn("what-if", waits["why"])


if __name__ == "__main__":
    unittest.main()
