"""Registration, the three surfaces end to end (with a fake Hermes fetch), and the plugin's promises:
self-contained, no network or file I/O of its own, manifest and README in step with the code."""
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false

import argparse
import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin  # noqa: E402

plugin = _plugin.load()
from claude_pacer import cli, core, pacing, report, usage  # noqa: E402
from claude_pacer.core import Pacer  # noqa: E402

NOW, snapshot, config = _plugin.NOW, _plugin.snapshot, _plugin.config
SOURCES = sorted(f for f in os.listdir(_plugin.PLUGIN_DIR) if f.endswith(".py"))


def strict_json(text):
    """``json.loads`` that refuses NaN / Infinity / -Infinity: Python accepts them by default, JSON does not."""
    def refuse(token):
        raise ValueError(f"not JSON: {token}")
    return json.loads(text, parse_constant=refuse)


class RecordingCtx:
    """Stands in for Hermes' PluginContext: records what register() asks for."""

    def __init__(self):
        self.tools, self.commands, self.cli_commands = [], [], []

    def get_config(self, key, default=None):
        return {"cache_seconds": 0}.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools.append(dict(name=name, toolset=toolset, schema=schema, handler=handler, **kw))

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands.append(dict(name=name, handler=handler, description=description, args_hint=args_hint))

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli_commands.append(dict(name=name, help=help, setup_fn=setup_fn, handler_fn=handler_fn))


def pacer(**snap):
    return Pacer(get_config=config(), fetch=lambda: snapshot(**snap), now=lambda: NOW)


def run_cli(p, argv):
    parser = argparse.ArgumentParser(prog="hermes pace")
    cli.setup_parser(parser)
    args = parser.parse_args(argv)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = p.cli(args)
    return rc, out.getvalue()


class TestRegister(unittest.TestCase):
    def setUp(self):
        self.ctx = RecordingCtx()
        plugin.register(self.ctx)

    def test_registers_one_tool_one_slash_command_one_cli_command(self):
        self.assertEqual([t["name"] for t in self.ctx.tools], ["claude_pace"])
        tool = self.ctx.tools[0]
        self.assertEqual((tool["toolset"], tool["schema"]["name"]), ("claude_pacer", "claude_pace"))
        self.assertEqual(tool["schema"]["parameters"], {"type": "object", "properties": {}, "required": []})
        self.assertEqual([c["name"] for c in self.ctx.commands], ["pace"])
        self.assertEqual([c["name"] for c in self.ctx.cli_commands], ["pace"])

    def test_registration_reads_nothing(self):
        calls = []

        class Spy(RecordingCtx):
            def get_config(self, key, default=None):
                calls.append(key)
                return default
        plugin.register(Spy())
        self.assertEqual(calls, [])  # settings are read per call, never at load

    @unittest.skipIf(importlib.util.find_spec("agent") is not None,
                     "Hermes is importable here: the default fetch would make a real request")
    def test_registered_tool_fails_open_without_hermes(self):
        usage.clear_cache()
        result = json.loads(self.ctx.tools[0]["handler"]({}))
        self.assertEqual((result["mode"], result["rule"]), ("normal", "usage_unknown"))
        self.assertIn("ModuleNotFoundError", result["usage_error"])


class TestTool(unittest.TestCase):
    def test_returns_mode_reason_windows_pace_and_hours_to_reset(self):
        result = json.loads(pacer(week=55, week_reset_h=84).tool({}, task_id="t1", session_id="s1"))
        self.assertEqual((result["mode"], result["reason"]), ("light", "over pace; week 55% vs pace 50%, resets in 84.0h"))
        self.assertEqual((result["week"]["pace_pct"], result["week"]["resets_in_h"]), (50.0, 84.0))
        self.assertEqual((result["five_hour"]["used_pct"], result["five_hour"]["resets_in_h"]), (3.0, 4.9))
        self.assertIn("guidance", result)

    def test_never_raises(self):
        def boom():
            raise RuntimeError("down")
        broken_clock = Pacer(get_config=config(), fetch=lambda: snapshot(), now=lambda: 1 / 0)
        for p in (Pacer(get_config=config(), fetch=boom, now=lambda: NOW), broken_clock):
            result = json.loads(p.tool({}))
            self.assertEqual(result["mode"], "normal")
        self.assertEqual(json.loads(broken_clock.tool({}))["reason"], "claude-pacer error (ZeroDivisionError), fail open")

    def test_settings_apply_per_call(self):
        values = {"cache_seconds": 0}
        p = Pacer(get_config=lambda k, d=None: values.get(k, d), fetch=lambda: snapshot(week=45, week_reset_h=84),
                  now=lambda: NOW)
        self.assertEqual(json.loads(p.tool({}))["mode"], "normal")
        values["reserve_pct"] = 40
        result = json.loads(p.tool({}))
        self.assertEqual((result["mode"], result["week"]["reserve_pct"], result["week"]["reserve_status"]),
                         ("light", 40, "at risk"))
        values["reserve_pct"] = "lots"
        result = json.loads(p.tool({}))
        self.assertEqual(result["config_warnings"], ["reserve_pct: 'lots' is not a number in 0..100, using 20"])
        self.assertEqual(result["week"]["reserve_pct"], 20)


class TestSlash(unittest.TestCase):
    def test_one_line(self):
        line = pacer().slash("", session_id="s", platform="cli")
        self.assertNotIn("\n", line)
        self.assertTrue(line.startswith("🟢 normal · on pace · week 13% "))

    def test_shows_the_reserve(self):
        self.assertIn(" · reserve 20%: on track · ", pacer().slash(""))
        kept = Pacer(get_config=config(reserve_pct=30), fetch=lambda: snapshot(week=42, week_reset_h=96), now=lambda: NOW)
        self.assertIn(" · reserve 30%: at risk · ", kept.slash(""))
        rc, out = run_cli(kept, [])
        self.assertEqual(out.strip(), kept.slash(""))  # `hermes pace` and /pace print the same line

    def test_json_help_and_unknown(self):
        self.assertEqual(json.loads(pacer().slash("json"))["mode"], "normal")
        self.assertEqual(pacer().slash("help"), core.SLASH_USAGE)
        self.assertTrue(pacer().slash("now please").startswith("Unknown argument 'now please'."))


class TestCli(unittest.TestCase):
    def test_line_json_and_what_if(self):
        rc, out = run_cli(pacer(), [])
        self.assertEqual((rc, out.count("\n")), (0, 1))
        self.assertTrue(out.startswith("🟢 normal"))
        rc, out = run_cli(pacer(), ["--json"])
        self.assertEqual(json.loads(out)["week"]["used_pct"], 13.0)
        rc, out = run_cli(pacer(), ["--week", "70"])
        self.assertTrue(out.startswith("🔴 skip · far over pace (what-if) · week 70%"), out)

    def test_gate_prints_exactly_one_json_line(self):
        for argv, snap, expected in (
                (["gate"], {}, True),
                (["gate"], {"h5": 90}, False),
                (["gate", "--priority", "vital"], {"h5": 90}, True),
                (["gate", "--priority", "vital"], {"h5": 96}, True),    # pacing never stops a vital job
                (["gate", "--priority", "vital"], {"h5": 100}, False),  # a used-up limit does
                (["gate", "--priority", "optional"], {"week": 55, "week_reset_h": 84}, False),
                (["gate", "--priority", "spare"], {}, False),
                (["gate", "--priority", "spare"], {"week": 40, "week_reset_h": 20}, True),
                (["--week", "99", "gate"], {}, False),
                (["--week", "90", "gate", "--priority", "vital"], {}, True)):
            rc, out = run_cli(pacer(**snap), argv)
            self.assertEqual((rc, out.count("\n")), (0, 1), argv)
            doc = strict_json(out)
            self.assertIs(doc["wakeAgent"], expected, (argv, snap))
            priority = argv[argv.index("--priority") + 1] if "--priority" in argv else "normal"
            self.assertEqual(doc["priority"], priority)
            self.assertTrue(doc["why"].startswith(f"{priority} job, "), doc["why"])
            self.assertEqual(set(doc), {"wakeAgent", "priority", "why"} | ({"context"} if expected else set())
                             | ({"what_if"} if "--week" in argv and not expected else set()))

    def test_what_if_refuses_non_finite_percentages(self):
        """`--week nan` used to reach the gate JSON as NaN (and `inf` read as a hard stop): argparse refuses it."""
        for flag in ("--week", "--h5"):
            for bad in ("nan", "NaN", "inf", "-inf", "Infinity", "1e999", "lots"):
                for argv in ([f"{flag}={bad}", "gate"], [f"{flag}={bad}"]):
                    with self.subTest(argv=argv):
                        out, err = io.StringIO(), io.StringIO()
                        with self.assertRaises(SystemExit) as stop, contextlib.redirect_stdout(out), \
                                contextlib.redirect_stderr(err):
                            run_cli(pacer(), argv)
                        self.assertEqual((stop.exception.code, out.getvalue()), (2, ""))
                        self.assertIn(f"argument {flag}: expected a finite percentage such as 70, got {bad!r}",
                                      err.getvalue())
        self.assertEqual((cli.percent(" 70 "), cli.percent("-5"), cli.percent("1e2")), (70.0, -5.0, 100.0))

    def test_gate_line_is_strict_json_even_if_a_nan_gets_in(self):
        """A non-finite what-if from code (the CLI refuses one) and a NaN inside the payload both fail open to a
        strict line instead of printing NaN."""
        self.assertEqual(pacer().gate(week=float("nan")), report.fail_open_gate())
        self.assertEqual(pacer().gate("spare", h5=float("inf")), report.fail_open_gate("spare"))  # counted as normal
        p = pacer()
        p.payload = lambda **_: {"mode": "light", "week": {"used_pct": float("nan")}}
        self.assertEqual(p.gate(), report.fail_open_gate())
        for priority in pacing.PRIORITIES:
            strict_json(report.fail_open_gate(priority))

    def test_gate_fails_open(self):
        p = pacer()
        p.payload = lambda **_: 1 / 0
        rc, out = run_cli(p, ["gate"])
        self.assertEqual((rc, json.loads(out)["context"]["budget_reason"]), (0, "gate error, fail open"))
        for priority in ("vital", "optional"):
            rc, out = run_cli(p, ["gate", "--priority", priority])
            self.assertEqual((json.loads(out)["wakeAgent"], json.loads(out)["priority"]), (True, priority))
        rc, out = run_cli(p, ["gate", "--priority", "spare"])  # an error counts as normal: spare jobs wait
        self.assertEqual((rc, out), (0, report.fail_open_gate("spare") + "\n"))
        self.assertIs(json.loads(out)["wakeAgent"], False)

    def test_bad_priority_is_an_argparse_error(self):
        for argv in (["gate", "--priority", "urgent"], ["gate", "--priority", "skip"], ["gate", "--min-mode", "heavy"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as stop, \
                    contextlib.redirect_stderr(io.StringIO()):
                run_cli(pacer(), argv)
            self.assertEqual(stop.exception.code, 2)

    def test_a_gated_cron_stops_at_the_reserve_line(self):
        """Ten hours before the reset the pace line sits on the target: a cron runs up to it, then skips. A vital
        one runs on into the reserve, until the weekly limit is used up (100 %)."""
        def kept(reserve=None):
            values = {} if reserve is None else {"reserve_pct": reserve}
            return Pacer(get_config=config(**values), fetch=lambda: snapshot(week_reset_h=10), now=lambda: NOW)
        for p, week, wakes in ((kept(), "79", True), (kept(), "80", False), (kept(30), "69", True), (kept(30), "70", False)):
            rc, out = run_cli(p, ["--week", week, "gate"])
            self.assertEqual((rc, strict_json(out)["wakeAgent"]), (0, wakes), (week, out))
        for week, wakes in (("80", True), ("97.5", True), ("98", True), ("99.9", True), ("100", False)):
            rc, out = run_cli(kept(), ["--week", week, "gate", "--priority", "vital"])
            self.assertEqual(strict_json(out)["wakeAgent"], wakes, (week, out))


def _imports(tree):
    """(module-level imports, every import) as module names; relative imports excluded."""
    top, everywhere = set(), set()
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module]
        everywhere.update(names)
        if node in tree.body:
            top.update(names)
    return top, everywhere


class TestPromises(unittest.TestCase):
    def parsed(self):
        for name in SOURCES:
            with open(os.path.join(_plugin.PLUGIN_DIR, name), encoding="utf-8") as fh:
                yield name, ast.parse(fh.read())

    def test_stdlib_at_import_time_and_only_documented_hermes_helpers_inside_functions(self):
        hermes = set()
        for name, tree in self.parsed():
            top, everywhere = _imports(tree)
            for mod in top:
                self.assertTrue(mod == "__future__" or mod.split(".")[0] in sys.stdlib_module_names, (name, mod))
            hermes |= {m for m in everywhere if m != "__future__" and m.split(".")[0] not in sys.stdlib_module_names}
        self.assertEqual(hermes, {"agent.account_usage", "hermes_constants"})  # both public plugin surfaces

    def test_no_network_shell_or_file_writes_of_its_own(self):
        banned = {"socket", "ssl", "http", "urllib", "httpx", "requests", "subprocess", "shutil", "tempfile", "pathlib"}
        for name, tree in self.parsed():
            _top, everywhere = _imports(tree)
            self.assertFalse({m.split(".")[0] for m in everywhere} & banned, name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"open", "exec", "eval", "__import__"}, name)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
                    self.assertEqual(node.attr, "environ", name)  # os is only read for HERMES_HOME

    def test_self_contained(self):
        """Nothing from the scripts this grew out of, and no sys.path games to reach them."""
        for name in SOURCES:
            with open(os.path.join(_plugin.PLUGIN_DIR, name), encoding="utf-8") as fh:
                text = fh.read()
            for word in ("usage_windows", "autonomy_gate", "sys.path", "importlib"):
                self.assertNotIn(word, text, name)


class TestManifestAndReadme(unittest.TestCase):
    """plugin.yaml / README.md parsed by hand: the bare Python these tests run on has no YAML module."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(_plugin.PLUGIN_DIR, "plugin.yaml"), encoding="utf-8") as fh:
            cls.manifest = fh.read()
        with open(os.path.join(_plugin.PLUGIN_DIR, "README.md"), encoding="utf-8") as fh:
            cls.readme = fh.read()

    def block(self, key):
        lines = self.manifest.split(f"\n{key}:\n", 1)[1].split("\n")
        return [line for line in lines[:next((i for i, line in enumerate(lines) if line and not line.startswith(" ")),
                                             len(lines))]]

    def test_declared_tools_match_registrations(self):
        declared = [line.strip()[2:] for line in self.block("provides_tools") if line.strip().startswith("- ")]
        ctx = RecordingCtx()
        plugin.register(ctx)
        self.assertEqual(declared, [t["name"] for t in ctx.tools])
        self.assertIn("provides_hooks: []", self.manifest)

    def test_config_schema_matches_the_code_defaults(self):
        schema, key = {}, None
        for line in self.block("config_schema"):
            m = re.match(r"^  ([a-z0-9_]+):$", line)
            if m:
                key = m.group(1)
            m = re.match(r"^    default: (.+)$", line)
            if m:
                schema[key] = float(m.group(1))
        expected = {f: float(getattr(pacing.DEFAULTS, f)) for f in pacing.RANGES}
        expected["cache_seconds"] = core.CACHE_SECONDS
        self.assertEqual(schema, expected)

    def test_readme_documents_every_setting_and_surface(self):
        for key in list(pacing.RANGES) + ["cache_seconds"]:
            self.assertIn(f"`{key}`", self.readme, key)
        for surface in ("claude_pace", "/pace", "hermes pace gate", "--priority", '"wakeAgent": false', "cron_jobs",
                        "crons run: "):
            self.assertIn(surface, self.readme, surface)
        section = self.readme.split("\n### Job priorities\n", 1)[1].split("\n## ", 1)[0]
        for priority in pacing.PRIORITIES:
            self.assertIn(f"| `{priority}`", section, priority)
            self.assertIn(priority, cli.GATE_DESCRIPTION)
        self.assertIn('exec hermes pace gate --priority vital "$@"', self.readme)  # the vital shim recipe
        self.assertNotIn("--min-mode", self.readme)

    def test_nothing_promises_a_stop_for_vital_jobs_or_an_untouched_reserve(self):
        """0.4.0 (Ruben, Nov 8): vital jobs are never stopped by pacing and may use the reserve. The retired vital
        stops appear only where the docs say they are gone, and no text puts a 95/98 % stop on vital jobs."""
        texts = {"plugin.yaml": self.manifest, "README.md": self.readme}
        docs = os.path.join(_plugin.PLUGIN_DIR, "docs")
        for name in sorted(os.listdir(docs)):
            if name.endswith(".md"):
                with open(os.path.join(docs, name), encoding="utf-8") as fh:
                    texts[f"docs/{name}"] = fh.read()
        texts["cli"] = cli.GATE_DESCRIPTION
        texts["tool schema"] = core.TOOL_SCHEMA["description"]
        self.assertNotIn("vital_h5_stop_pct", self.manifest)
        self.assertNotIn("vital_week_stop_pct", self.manifest)
        for name, text in texts.items():
            paragraphs = text.split("\n\n")
            for para in paragraphs:
                if "vital_h5_stop_pct" in para or "vital_week_stop_pct" in para:
                    self.assertIn("no longer used", para, (name, para))
            for stale in ("vital stop", "real limits are close", "real limit is close", "still there at the reset",
                          "never spend into the reserve"):
                self.assertNotIn(stale, text, (name, stale))
        reserve = texts["docs/reserve.md"].split("\n## Vital jobs and the reserve\n", 1)[1]
        self.assertIn("Pacing never stops", reserve)

    def test_the_reserve_is_one_setting(self):
        """The reserve replaced the weekly target, the weekly hard stop and the absolute use-it-or-lose-it
        ceiling: one value the user sets, documented with a recipe and the three statuses /pace prints."""
        for gone in ("week_target_pct", "week_skip_pct", "heavy_reset_week_max"):
            self.assertNotIn(gone, self.manifest)
            self.assertNotIn(gone, pacing.RANGES)
            self.assertNotIn(f"`{gone}`", self.readme)
        section = self.readme.split("\n## Keep a reserve\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("hermes config set plugins.entries.claude-pacer.settings.reserve_pct ", section)
        for status in (pacing.RESERVE_ON_TRACK, pacing.RESERVE_AT_RISK, pacing.RESERVE_IN_USE):
            self.assertIn(f"| {status} |", section)
        self.assertIn("reserve 20%: on track", self.readme)  # the example /pace line
        self.assertIn("reserve_pct", core.TOOL_SCHEMA["description"])

    def description(self):
        """plugin.yaml's folded ``description: >`` block on one line: the text plugin listings show."""
        lines = self.manifest.split("\ndescription: >\n", 1)[1].split("\n")
        body = lines[:next(i for i, line in enumerate(lines) if line and not line.startswith(" "))]
        return " ".join(" ".join(body).split())

    def test_disclosure_names_the_refresh_a_check_can_trigger(self):
        """Security scan finding (hermes-security, informational): a check runs Hermes' Anthropic usage fetch, which
        first refreshes an expired borrowed Claude Code login (a POST to the OAuth token endpoint plus a rewrite of the
        Claude Code credentials file); read_only=True does not stop that. The manifest and the README must both say
        so, and neither may promise a lone read-only GET."""
        readme = " ".join(self.readme.split("\n## Disclosure\n", 1)[1].split("\n## ", 1)[0].split())
        for name, text in (("plugin.yaml description", self.description()), ("README Disclosure", readme)):
            with self.subTest(name):
                low = text.lower()
                for needed in ("claude code", "expired", "refresh", "token endpoint", "credentials file"):
                    self.assertIn(needed, low, f"{name} must disclose the refresh ({needed!r} missing)")
                for stale in ("one read-only get per check", "no other network access",
                              "never reads, holds or refreshes", "never reads, holds, refreshes"):
                    self.assertNotIn(stale, low, f"{name} still says {stale!r}")

    def test_read_only_flag_is_not_described_as_a_no_refresh_guarantee(self):
        doc = " ".join((usage.fetch_snapshot.__doc__ or "").split()).lower()
        self.assertNotIn("never rotates or refreshes", doc)
        self.assertIn("claude code", doc)

    def test_tool_description_makes_no_read_only_promise(self):
        """The model reads this text: like the disclosure, it must say a check can make Hermes refresh an expired
        Claude Code sign-in, not call the tool read-only."""
        text = " ".join(core.TOOL_SCHEMA["description"].split()).lower()
        for stale in ("read-only", "read only"):
            self.assertNotIn(stale, text)
        for needed in ("claude code", "expired", "refresh"):
            self.assertIn(needed, text)

    def test_recipes_follow_the_profile_home(self):
        """Cron runs scripts only from the profile's $HERMES_HOME/scripts, and ~/.hermes is only the default
        profile's home: the README's shell recipes and `hermes pace gate --help` must not hardcode it."""
        blocks = [b.split("```", 1)[0] for b in self.readme.split("```bash\n")[1:]]
        self.assertGreaterEqual(len(blocks), 2)
        for block in blocks:
            self.assertNotIn("~/.hermes", block)
        self.assertIn('scripts="$(dirname "$(hermes config path)")/scripts"', self.readme)
        self.assertIn("$HERMES_HOME/scripts", cli.GATE_DESCRIPTION)
        self.assertNotIn("~/.hermes", cli.GATE_DESCRIPTION)


if __name__ == "__main__":
    unittest.main()
