"""Generated, self-grading coding tasks for the kvleak before/after benchmark.

Every task builds a small stdlib-only Python repo in a directory, gives the
agent a prompt, and grades the result from *outside* the repo (hidden tests
and reference answers live in the run directory, not in the repo). Each task
also ships a `solve()` that applies the reference fix, so the graders can be
self-tested without a model: `python bench/run.py --selftest`.

The tasks are designed around what kvleak's hooks and rules act on: long
shell output (verbose test suites, build logs, big files), many-step
exploration across files, and loops. They are synthetic; the point is a
controlled A/B, not a difficulty benchmark.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

PY = sys.executable


@dataclass
class Task:
    name: str
    prompt: str
    setup: Callable[[Path, Path], None]  # (repo, hidden) -> None
    grade: Callable[[Path, Path], Dict]  # (repo, hidden) -> {"score": 0..1, ...}
    solve: Callable[[Path, Path], None]  # reference fix, for self-test
    seed: int = 0
    tags: List[str] = field(default_factory=list)


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(cmd: List[str], cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=env)


def _unittest(cwd: Path, test_dir: str = "tests") -> Dict:
    """Run unittest discovery; returns passed/total parsed from the verbose output."""
    r = _run([PY, "-m", "unittest", "discover", "-s", test_dir, "-t", ".", "-v"], cwd)
    out = r.stdout + r.stderr
    ran = re.search(r"Ran (\d+) tests?", out)
    total = int(ran.group(1)) if ran else 0
    failed = len(re.findall(r"^(?:FAIL|ERROR): ", out, re.M))
    return {"total": total, "passed": max(total - failed, 0), "ok": r.returncode == 0 and total > 0}


def _copy_tree(src: Path, dst: Path) -> None:
    for p in src.rglob("*"):
        if p.is_file():
            q = dst / p.relative_to(src)
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_bytes(p.read_bytes())


# ------------------------------------------------------------ function templates

# name -> (correct body, buggy body, [(args, expected)])
FUNCS = {
    "add_tax": ("def add_tax(amount, rate):\n    return round(amount * (1 + rate), 2)\n",
                "def add_tax(amount, rate):\n    return round(amount * rate, 2)\n",
                [("100, 0.2", "120.0"), ("10, 0.075", "10.75"), ("0, 0.5", "0.0"), ("19.99, 0.1", "21.99")]),
    "clamp": ("def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n",
              "def clamp(x, lo, hi):\n    return max(lo, min(x, hi - 1))\n",
              [("5, 0, 10", "5"), ("-3, 0, 10", "0"), ("15, 0, 10", "10"), ("10, 0, 10", "10")]),
    "median": ("def median(xs):\n    s = sorted(xs)\n    n = len(s)\n    if n == 0:\n        raise ValueError('empty')\n"
               "    mid = n // 2\n    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2\n",
               "def median(xs):\n    s = sorted(xs)\n    n = len(s)\n    if n == 0:\n        raise ValueError('empty')\n"
               "    return s[n // 2]\n",
               [("[3, 1, 2]", "2"), ("[4, 1, 3, 2]", "2.5"), ("[7]", "7"), ("[1, 2, 3, 4, 5, 6]", "3.5")]),
    "slugify": ("def slugify(s):\n    s = re.sub(r'[^a-z0-9]+', '-', s.lower())\n    return s.strip('-')\n",
                "def slugify(s):\n    s = re.sub(r'[^a-z0-9]+', '-', s.lower())\n    return s\n",
                [("'Hello World'", "'hello-world'"), ("'  A  B '", "'a-b'"), ("'x'", "'x'"), ("'Up & Down!'", "'up-down'")]),
    "chunk": ("def chunk(xs, n):\n    return [xs[i:i + n] for i in range(0, len(xs), n)]\n",
              "def chunk(xs, n):\n    return [xs[i:i + n] for i in range(0, len(xs) - 1, n)]\n",
              [("[1, 2, 3, 4, 5], 2", "[[1, 2], [3, 4], [5]]"), ("[], 3", "[]"), ("[1, 2], 5", "[[1, 2]]"), ("[1, 2, 3], 1", "[[1], [2], [3]]")]),
    "pct_change": ("def pct_change(a, b):\n    if a == 0:\n        return None\n    return round((b - a) / a * 100, 1)\n",
                   "def pct_change(a, b):\n    if a == 0:\n        return None\n    return round((b - a) / b * 100, 1)\n",
                   [("100, 150", "50.0"), ("200, 100", "-50.0"), ("0, 5", "None"), ("80, 100", "25.0")]),
    "is_palindrome": ("def is_palindrome(s):\n    t = ''.join(c for c in s.lower() if c.isalnum())\n    return t == t[::-1]\n",
                      "def is_palindrome(s):\n    t = ''.join(c for c in s if c.isalnum())\n    return t == t[::-1]\n",
                      [("'Racecar'", "True"), ("'A man, a plan, a canal: Panama'", "True"), ("'abc'", "False"), ("''", "True")]),
    "word_count": ("def word_count(s):\n    return len(s.split())\n",
                   "def word_count(s):\n    return len(s.split(' '))\n",
                   [("'a b c'", "3"), ("'  a   b '", "2"), ("''", "0"), ("'one'", "1")]),
    "running_total": ("def running_total(xs):\n    out, t = [], 0\n    for x in xs:\n        t += x\n        out.append(t)\n    return out\n",
                      "def running_total(xs):\n    out, t = [], 0\n    for x in xs:\n        out.append(t)\n        t += x\n    return out\n",
                      [("[1, 2, 3]", "[1, 3, 6]"), ("[]", "[]"), ("[5]", "[5]"), ("[1, -1, 1]", "[1, 0, 1]")]),
    "safe_div": ("def safe_div(a, b, default=0):\n    return a / b if b else default\n",
                 "def safe_div(a, b, default=0):\n    return a / b if a else default\n",
                 [("6, 3", "2.0"), ("1, 0", "0"), ("1, 0, -1", "-1"), ("0, 5", "0.0")]),
    "dedupe": ("def dedupe(xs):\n    seen, out = set(), []\n    for x in xs:\n        if x not in seen:\n            seen.add(x)\n            out.append(x)\n    return out\n",
               "def dedupe(xs):\n    return list(set(xs))\n",
               [("[3, 1, 3, 2, 1]", "[3, 1, 2]"), ("[]", "[]"), ("['a', 'a']", "['a']"), ("[1, 2, 3]", "[1, 2, 3]")]),
    "parse_bool": ("def parse_bool(s):\n    return str(s).strip().lower() in ('1', 'true', 'yes', 'on')\n",
                   "def parse_bool(s):\n    return str(s).strip().lower() in ('1', 'true', 'yes')\n",
                   [("'yes'", "True"), ("'ON'", "True"), ("'0'", "False"), ("' true '", "True")]),
}
MODULES = ["pricing", "textutil", "stats", "collections_ext", "validation", "formatting", "numeric", "strings",
           "sequences", "parsing"]


def _module_source(funcs: List[str], buggy: Optional[str]) -> str:
    parts = ['"""Generated helpers."""\n\nimport re  # noqa: F401\n\nfrom app import log\n\nlog.debug("loading %s", __name__)\n\n']
    for f in funcs:
        parts.append((FUNCS[f][1] if f == buggy else FUNCS[f][0]) + "\n\n")
    return "".join(parts)


def _tests_source(module: str, funcs: List[str]) -> str:
    lines = ["import unittest\n", "from app import log\n", "from app.{} import {}\n\n\n".format(module, ", ".join(funcs)),
             "class Test{}(unittest.TestCase):\n".format(module.title().replace("_", "")),
             "    def setUp(self):\n        log.debug('fixture up for %s (db=memory, seed=%d)', self.id(), 42)\n\n"]
    for f in funcs:
        for i, (args, expected) in enumerate(FUNCS[f][2]):
            lines.append("    def test_{}_{}(self):\n        log.debug('case %s args=%r', '{}', ({},))\n"
                         "        self.assertEqual({}({}), {})\n\n".format(f, i, f, args, f, args, expected))
    lines.append("\nif __name__ == '__main__':\n    unittest.main()\n")
    return "".join(lines)


APP_INIT = '''"""Generated application package."""
import logging
import sys

log = logging.getLogger("app")
_h = logging.StreamHandler(sys.stderr)
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
log.addHandler(_h)
log.setLevel(logging.DEBUG)
for _i in range({noise}):
    log.debug("init step %d: cache warm, %d entries, backend=%s", _i, _i * 37 % 1013, ["disk", "mem", "s3"][_i % 3])
'''


def _make_package(repo: Path, rnd: random.Random, n_modules: int, n_bugs: int, noise: int = 120):
    """Modules with tests; returns {module: buggy function}."""
    _w(repo / "app" / "__init__.py", APP_INIT.format(noise=noise))
    names = list(FUNCS)
    modules = MODULES[:n_modules]
    buggy_mods = rnd.sample(modules, n_bugs)
    bugs = {}
    for m in modules:
        funcs = rnd.sample(names, 6)
        bug = rnd.choice(funcs) if m in buggy_mods else None
        if bug:
            bugs[m] = bug
        _w(repo / "app" / (m + ".py"), _module_source(funcs, bug))
        _w(repo / "tests" / "test_{}.py".format(m), _tests_source(m, funcs))
    _w(repo / "tests" / "__init__.py", "")
    _w(repo / "run_tests.sh", "#!/bin/sh\n{} -m unittest discover -s tests -t . -v\n".format("python3"))
    os.chmod(repo / "run_tests.sh", 0o755)
    return bugs


# ----------------------------------------------------------------- 1 noisy-tests


def noisy_tests(seed: int) -> Task:
    rnd = random.Random(seed)

    def setup(repo: Path, hidden: Path):
        bugs = _make_package(repo, rnd, 10, 6, noise=400)
        _copy_tree(repo / "tests", hidden / "tests")
        _w(hidden / "bugs.json", json.dumps(bugs))
        _w(repo / "README.md", "# helpers\n\nRun the suite with `./run_tests.sh`. Some tests fail; the code is wrong, not the tests.\n")

    def grade(repo: Path, hidden: Path) -> Dict:
        bugs = json.loads((hidden / "bugs.json").read_text())
        work = hidden / "grade"
        if work.exists():
            subprocess.run(["rm", "-rf", str(work)])
        _copy_tree(repo, work)
        subprocess.run(["rm", "-rf", str(work / "tests")])
        _copy_tree(hidden / "tests", work / "tests")
        res = _unittest(work)
        fixed = 0
        for m, f in bugs.items():
            r = _run([PY, "-m", "unittest", "-v", "tests.test_{}".format(m)], work)
            out = r.stdout + r.stderr
            if not re.search(r"^(?:FAIL|ERROR): test_{}_".format(f), out, re.M):
                fixed += 1
        regressions = res["total"] - res["passed"]
        score = fixed / len(bugs) if res["ok"] else fixed / len(bugs) * 0.5
        return {"score": round(score, 3), "fixed": fixed, "bugs": len(bugs), "suite_ok": res["ok"], "remaining_failures": regressions}

    def solve(repo: Path, hidden: Path):
        bugs = json.loads((hidden / "bugs.json").read_text())
        for m, f in bugs.items():
            p = repo / "app" / (m + ".py")
            p.write_text(p.read_text().replace(FUNCS[f][1], FUNCS[f][0]))

    return Task("noisy-tests", "Run ./run_tests.sh. Several tests fail because of bugs in app/. Fix the code so the whole suite "
                "passes. Do not modify the tests.", setup, grade, solve, seed, ["shell-output", "loops"])


# -------------------------------------------------------------------- 2 log-hunt

CONFIG_PY = '''"""Runtime configuration."""
import re

DEFAULTS = {"cache.ttl_seconds": 300, "cache.max_entries": 10000, "http.timeout_seconds": 30}


def parse_duration(value):
    """Seconds from an int, a numeric string, or a string with a unit (s, m, h)."""
    return int(value)


def load(pairs):
    cfg = dict(DEFAULTS)
    for key, value in pairs:
        if key.endswith("_seconds"):
            cfg[key] = parse_duration(value)
        else:
            cfg[key] = int(value)
    return cfg
'''

CONFIG_FIX = '''def parse_duration(value):
    """Seconds from an int, a numeric string, or a string with a unit (s, m, h)."""
    if isinstance(value, (int, float)):
        return int(value)
    m = re.fullmatch(r"\\s*(\\d+)\\s*([smh]?)\\s*", str(value))
    if not m:
        raise ValueError("bad duration: %r" % (value,))
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]
'''

CONFIG_TESTS = '''import unittest
from app.config import parse_duration, load


class TestConfig(unittest.TestCase):
    def test_int(self):
        self.assertEqual(parse_duration(30), 30)

    def test_numeric_string(self):
        self.assertEqual(parse_duration("45"), 45)

    def test_units(self):
        self.assertEqual(parse_duration("5m"), 300)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("10s"), 10)

    def test_load(self):
        cfg = load([("cache.ttl_seconds", "5m"), ("cache.max_entries", "50")])
        self.assertEqual(cfg["cache.ttl_seconds"], 300)
        self.assertEqual(cfg["cache.max_entries"], 50)

    def test_bad(self):
        with self.assertRaises(ValueError):
            parse_duration("soon")
'''


def log_hunt(seed: int) -> Task:
    rnd = random.Random(seed)
    first_bad = "req-" + "".join(rnd.choice("0123456789abcdef") for _ in range(12))
    key = rnd.choice(["cache.ttl_seconds", "http.timeout_seconds"])
    value = rnd.choice(["5m", "2h", "90s"])

    def setup(repo: Path, hidden: Path):
        _w(repo / "app" / "__init__.py", "")
        _w(repo / "app" / "config.py", CONFIG_PY)
        t0 = datetime(2026, 9, 14, 2, 0, 0)
        paths = ["/api/orders", "/api/users", "/api/cart", "/health", "/api/search", "/api/checkout"]
        lines = []
        n = 130_000
        bad_at = rnd.randint(60_000, 100_000)
        cascade_until = bad_at + rnd.randint(900, 1500)
        cascade = 0
        t = t0
        for i in range(n):
            t += timedelta(milliseconds=rnd.randint(5, 60))
            rid = "req-" + "".join(rnd.choice("0123456789abcdef") for _ in range(12))
            path = rnd.choice(paths)
            ts = t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03d" % (t.microsecond // 1000)
            if i == bad_at - 1:
                lines.append("{} INFO  config: reload requested by admin (3 keys changed)".format(ts))
                lines.append("{} INFO  config: set {}={}".format(ts, key, value))
                lines.append("{} INFO  config: set cache.max_entries=20000".format(ts))
            if i == bad_at:
                rid = first_bad
                lines.append("{} INFO  {} GET {} start".format(ts, rid, path))
                lines.append("{} ERROR {} config.load failed: ValueError: invalid literal for int() with base 10: '{}'".format(ts, rid, value))
                lines.append("{} ERROR {} GET {} 500 (12ms)".format(ts, rid, path))
                continue
            if bad_at < i < cascade_until:
                if rnd.random() < 0.7:
                    cascade += 1
                    lines.append("{} INFO  {} GET {} start".format(ts, rid, path))
                    lines.append("{} WARN  {} cache: miss (config not loaded, using defaults)".format(ts, rid))
                    lines.append("{} ERROR {} GET {} 504 upstream timeout (30000ms)".format(ts, rid, path))
                    continue
            lines.append("{} INFO  {} GET {} start".format(ts, rid, path))
            if rnd.random() < 0.02:
                lines.append("{} WARN  {} slow query ({}ms)".format(ts, rid, rnd.randint(300, 900)))
            lines.append("{} INFO  {} GET {} 200 ({}ms)".format(ts, rid, path, rnd.randint(3, 80)))
        _w(repo / "logs" / "app.log", "\n".join(lines) + "\n")
        _w(repo / "tests" / "__init__.py", "")
        _w(repo / "tests" / "test_config.py", "import unittest\nfrom app.config import parse_duration\n\n\nclass T(unittest.TestCase):\n"
           "    def test_int(self):\n        self.assertEqual(parse_duration(30), 30)\n")
        _w(repo / "README.md", "# incident\n\nProduction had an outage last night; `logs/app.log` is the full log. The incident "
           "started with one request failing on a configuration reload, after which hundreds of requests timed out.\n")
        _w(hidden / "answer.json", json.dumps({"request_id": first_bad, "key": key, "cascade": cascade}))
        _w(hidden / "tests" / "test_config_hidden.py", CONFIG_TESTS)
        _w(hidden / "tests" / "__init__.py", "")

    def grade(repo: Path, hidden: Path) -> Dict:
        ans = json.loads((hidden / "answer.json").read_text())
        text = (repo / "ANSWER.md").read_text() if (repo / "ANSWER.md").exists() else ""
        rid_ok = ans["request_id"] in text
        key_ok = ans["key"] in text
        m = re.search(r"timeouts?:\s*([\d,]+)", text)
        count_ok = bool(m) and abs(int(m.group(1).replace(",", "")) - ans["cascade"]) <= 2
        work = hidden / "grade"
        subprocess.run(["rm", "-rf", str(work)])
        _copy_tree(repo, work)
        subprocess.run(["rm", "-rf", str(work / "tests")])
        _copy_tree(hidden / "tests", work / "tests")
        res = _unittest(work)
        tests = res["passed"] / res["total"] if res["total"] else 0.0
        return {"score": round(0.3 * rid_ok + 0.2 * key_ok + 0.2 * count_ok + 0.3 * tests, 3), "request_id_ok": rid_ok,
                "key_ok": key_ok, "count_ok": count_ok, "hidden_tests": "{}/{}".format(res["passed"], res["total"])}

    def solve(repo: Path, hidden: Path):
        ans = json.loads((hidden / "answer.json").read_text())
        _w(repo / "ANSWER.md", "request_id: {}\nconfig_key: {}\ntimeouts: {}\n".format(ans["request_id"], ans["key"], ans["cascade"]))
        p = repo / "app" / "config.py"
        p.write_text(p.read_text().replace('def parse_duration(value):\n    """Seconds from an int, a numeric string, or a string with a unit (s, m, h)."""\n    return int(value)\n', CONFIG_FIX))

    return Task("log-hunt", "There was an outage; logs/app.log has the full log (it is large). Find the FIRST request that failed "
                "and the configuration key whose new value triggered it, and count how many requests ended in a 504 timeout during the "
                "cascade that followed. Write ANSWER.md containing three lines: `request_id: <id>`, `config_key: <key>` and "
                "`timeouts: <count>`. Then fix the bug in app/config.py so that values like the one in "
                "the log are accepted (durations may carry an s/m/h unit, and plain numbers must keep working), and make sure "
                "`python3 -m unittest discover -s tests -t .` passes.", setup, grade, solve, seed, ["shell-output", "chains"])


# ------------------------------------------------------------------ 3 rename-api

USERS_PY = '''"""User store."""

_USERS = {i: {"id": i, "name": "user%d" % i, "tier": ["free", "pro", "team"][i % 3]} for i in range(1, 51)}


def fetch_user(id):
    """Return the user dict for `id`, or None when unknown."""
    return _USERS.get(id)
'''

USERS_FIX = '''"""User store."""

_USERS = {i: {"id": i, "name": "user%d" % i, "tier": ["free", "pro", "team"][i % 3]} for i in range(1, 51)}


def load_user(user_id, *, strict=False):
    """Return the user dict for `user_id`; None when unknown, or KeyError with strict=True."""
    user = _USERS.get(user_id)
    if user is None and strict:
        raise KeyError(user_id)
    return user
'''

CALLER_FORMS = [
    "from app.users import fetch_user\n\n\ndef {fn}(uid):\n    u = fetch_user(uid)\n    return u['name'] if u else 'anonymous'\n",
    "from app import users\n\n\ndef {fn}(uid):\n    u = users.fetch_user(uid)\n    return (u or {{}}).get('tier', 'none')\n",
    "from app.users import fetch_user as _fetch\n\n\ndef {fn}(uids):\n    return [(_fetch(i) or {{}}).get('name') for i in uids]\n",
    "import app.users\n\n\ndef {fn}(uid):\n    u = app.users.fetch_user(uid)\n    if u is None:\n        raise LookupError(uid)\n    return u['id']\n",
]
CALLER_FIXES = [
    "from app.users import load_user\n\n\ndef {fn}(uid):\n    u = load_user(uid)\n    return u['name'] if u else 'anonymous'\n",
    "from app import users\n\n\ndef {fn}(uid):\n    u = users.load_user(uid)\n    return (u or {{}}).get('tier', 'none')\n",
    "from app.users import load_user as _fetch\n\n\ndef {fn}(uids):\n    return [(_fetch(i) or {{}}).get('name') for i in uids]\n",
    "import app.users\n\n\ndef {fn}(uid):\n    u = app.users.load_user(uid)\n    if u is None:\n        raise LookupError(uid)\n    return u['id']\n",
]
CALLER_TESTS = {
    0: "self.assertEqual({fn}(1), 'user1'); self.assertEqual({fn}(999), 'anonymous')",
    1: "self.assertEqual({fn}(2), 'team'); self.assertEqual({fn}(999), 'none')",
    2: "self.assertEqual({fn}([1, 999]), ['user1', None])",
    3: "self.assertEqual({fn}(3), 3); self.assertRaises(LookupError, {fn}, 999)",
}


def rename_api(seed: int) -> Task:
    rnd = random.Random(seed)
    n = 40
    forms = [rnd.randrange(4) for _ in range(n)]
    pkgs = ["services", "jobs", "api"]

    def setup(repo: Path, hidden: Path):
        _w(repo / "app" / "__init__.py", "")
        _w(repo / "app" / "users.py", USERS_PY)
        tests = ["import unittest\n", "from app.users import fetch_user\n"]
        hidden_tests = ["import unittest\n", "from app.users import load_user\n"]
        body = []
        for i in range(n):
            mod, fn, pkg = "svc%02d" % i, "handle%02d" % i, pkgs[i % 3]
            _w(repo / "app" / pkg / (mod + ".py"), CALLER_FORMS[forms[i]].format(fn=fn))
            _w(repo / "app" / pkg / "__init__.py", "")
            tests.append("from app.{}.{} import {}\n".format(pkg, mod, fn))
            hidden_tests.append("from app.{}.{} import {}\n".format(pkg, mod, fn))
            body.append("    def test_{}(self):\n        {}\n\n".format(mod, CALLER_TESTS[forms[i]].format(fn=fn)))
        cls = "\n\nclass TestServices(unittest.TestCase):\n" + "".join(body)
        _w(repo / "tests" / "__init__.py", "")
        _w(repo / "tests" / "test_services.py", "".join(tests) + cls + "    def test_fetch(self):\n        self.assertEqual(fetch_user(1)['name'], 'user1')\n        self.assertIsNone(fetch_user(0))\n")
        _w(hidden / "tests" / "__init__.py", "")
        _w(hidden / "tests" / "test_services_hidden.py", "".join(hidden_tests) + cls +
           "    def test_load(self):\n        self.assertEqual(load_user(1)['name'], 'user1')\n        self.assertIsNone(load_user(0))\n"
           "        self.assertRaises(KeyError, load_user, 0, strict=True)\n        self.assertEqual(load_user(5, strict=True)['id'], 5)\n")
        _w(repo / "docs" / "api.md", "# API\n\n## `fetch_user(id)`\n\nReturns the user dict for `id`, or `None`.\n\nExample:\n\n```python\nfrom app.users import fetch_user\nfetch_user(1)\n```\n")
        _w(repo / "README.md", "# users service\n\nTests: `python3 -m unittest discover -s tests -t . -v`\n")

    def grade(repo: Path, hidden: Path) -> Dict:
        work = hidden / "grade"
        subprocess.run(["rm", "-rf", str(work)])
        _copy_tree(repo, work)
        subprocess.run(["rm", "-rf", str(work / "tests")])
        _copy_tree(hidden / "tests", work / "tests")
        res = _unittest(work)
        tests = res["passed"] / res["total"] if res["total"] else 0.0
        leftovers = 0
        for p in list((repo / "app").rglob("*.py")) + [repo / "docs" / "api.md"]:
            if p.exists() and "fetch_user" in p.read_text():
                leftovers += 1
        return {"score": round(tests * (1.0 if leftovers == 0 else 0.5), 3), "hidden_tests": "{}/{}".format(res["passed"], res["total"]),
                "files_still_mentioning_fetch_user": leftovers}

    def solve(repo: Path, hidden: Path):
        _w(repo / "app" / "users.py", USERS_FIX)
        for i in range(n):
            mod, fn, pkg = "svc%02d" % i, "handle%02d" % i, pkgs[i % 3]
            _w(repo / "app" / pkg / (mod + ".py"), CALLER_FIXES[forms[i]].format(fn=fn))
        p = repo / "docs" / "api.md"
        p.write_text(p.read_text().replace("fetch_user(id)", "load_user(user_id, *, strict=False)").replace("fetch_user", "load_user"))
        p = repo / "tests" / "test_services.py"
        p.write_text(p.read_text().replace("fetch_user", "load_user"))

    return Task("rename-api", "Rename the function `fetch_user(id)` in app/users.py to `load_user(user_id, *, strict=False)`: "
                "with strict=False it returns None for an unknown id as before; with strict=True it raises KeyError. Update every "
                "caller in the codebase (they import it in different ways), the tests, and docs/api.md, so that nothing refers "
                "to `fetch_user` any more and `python3 -m unittest discover -s tests -t .` passes.",
                setup, grade, solve, seed, ["chains", "edits"])


# ----------------------------------------------------------------- 4 build-error

BUILD_PY = '''"""Toy build: compiles every module under src/ and writes build/manifest.json."""
import hashlib
import json
import os
import py_compile
import sys
import time

SRC, OUT = "src", "build"
errors = []
manifest = {}
os.makedirs(OUT, exist_ok=True)
files = sorted(f for f in os.listdir(SRC) if f.endswith(".py"))
for name in files:
    path = os.path.join(SRC, name)
    for step in range(1, {steps} + 1):
        print("[%s] step %3d/{steps}: %s" % (name, step, ["parse", "resolve imports", "typecheck", "optimize", "emit"][step % 5]))
    try:
        py_compile.compile(path, cfile=os.path.join(OUT, name + "c"), doraise=True)
        manifest[name] = hashlib.sha1(open(path, "rb").read()).hexdigest()
        print("[%s] ok" % name)
    except py_compile.PyCompileError as exc:
        errors.append(name)
        print("[%s] error: %s" % (name, str(exc).strip().splitlines()[-1]))
        print("[%s] continuing with next module" % name)
json.dump(manifest, open(os.path.join(OUT, "manifest.json"), "w"), indent=1)
if errors:
    print("BUILD FAILED (%d error%s)" % (len(errors), "s" if len(errors) > 1 else ""))
    sys.exit(1)
print("BUILD OK (%d modules)" % len(files))
'''

MODULE_TMPL = '''"""Module {i}."""


def value_{i}(x):
    return (x * {a} + {b}) % 97


def label_{i}(x):
    return "m{i}:%d" % value_{i}(x)


TABLE_{i} = {{k: value_{i}(k) for k in range(8)}}
'''


def build_error(seed: int) -> Task:
    rnd = random.Random(seed)
    n = 80
    bad, bad2 = rnd.sample(range(10, 70), 2)
    a, b = rnd.randint(2, 40), rnd.randint(1, 90)
    a2, b2 = rnd.randint(2, 40), rnd.randint(1, 90)

    def setup(repo: Path, hidden: Path):
        for i in range(n):
            if i == bad:  # an unbalanced paren on the return line
                src = MODULE_TMPL.format(i=i, a=a, b=b).replace("return (x * {} + {}) % 97".format(a, b), "return (x * {} + {} % 97".format(a, b))
            elif i == bad2:  # a def line without its colon
                src = MODULE_TMPL.format(i=i, a=a2, b=b2).replace("def label_{i}(x):".format(i=i), "def label_{i}(x)".format(i=i))
            else:
                src = MODULE_TMPL.format(i=i, a=rnd.randint(2, 40), b=rnd.randint(1, 90))
            _w(repo / "src" / ("mod_%02d.py" % i), src)
        _w(repo / "build.py", BUILD_PY.replace("{steps}", "220"))
        _w(repo / "README.md", "# build\n\n`python3 build.py` compiles src/ into build/. It currently fails.\n")
        _w(hidden / "expect.json", json.dumps({"bad": bad, "a": a, "b": b, "bad2": bad2, "a2": a2, "b2": b2}))

    def grade(repo: Path, hidden: Path) -> Dict:
        exp = json.loads((hidden / "expect.json").read_text())
        r = _run([PY, "build.py"], repo)
        build_ok = r.returncode == 0 and "BUILD OK" in r.stdout
        check = _run([PY, "-c", "import sys; sys.path.insert(0, 'src'); import mod_%02d as m; print(m.value_%d(5), m.label_%d(5))" % (exp["bad"], exp["bad"], exp["bad"])], repo)
        expected = "{} m{}:{}".format((5 * exp["a"] + exp["b"]) % 97, exp["bad"], (5 * exp["a"] + exp["b"]) % 97)
        behaviour_ok = check.stdout.strip() == expected
        check2 = _run([PY, "-c", "import sys; sys.path.insert(0, 'src'); import mod_%02d as m; print(m.label_%d(7))" % (exp["bad2"], exp["bad2"])], repo)
        behaviour2_ok = check2.stdout.strip() == "m{}:{}".format(exp["bad2"], (7 * exp["a2"] + exp["b2"]) % 97)
        return {"score": round(0.4 * build_ok + 0.3 * behaviour_ok + 0.3 * behaviour2_ok, 3), "build_ok": build_ok,
                "behaviour_ok": behaviour_ok, "behaviour2_ok": behaviour2_ok}

    def solve(repo: Path, hidden: Path):
        p = repo / "src" / ("mod_%02d.py" % bad)
        p.write_text(p.read_text().replace("return (x * {} + {} % 97".format(a, b), "return (x * {} + {}) % 97".format(a, b)))
        p = repo / "src" / ("mod_%02d.py" % bad2)
        p.write_text(p.read_text().replace("def label_{}(x)\n".format(bad2), "def label_{}(x):\n".format(bad2)))

    return Task("build-error", "`python3 build.py` fails. Find out why and fix the source so the build succeeds. Keep every "
                "module's behaviour (the functions must return what they were meant to return); do not delete or stub modules.",
                setup, grade, solve, seed, ["shell-output"])


# -------------------------------------------------------------- 5 data-aggregate

AGG_PY = '''"""Aggregate events.csv per event type. See README.md for the spec."""
import csv
import json
import sys
from datetime import datetime


def main(path="data/events.csv"):
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ts = datetime.strptime(row["ts"][:19], "%Y-%m-%dT%H:%M:%S")
            d = out.setdefault(row["event"], {"users": set(), "amount": 0.0, "days": set()})
            d["users"].add(row["user"])
            d["amount"] += float(row["amount"])
            d["days"].add(ts.date().isoformat())
    result = {k: {"users": len(v["users"]), "amount": round(v["amount"], 2), "days": len(v["days"])}
              for k, v in sorted(out.items())}
    json.dump(result, sys.stdout, indent=1, sort_keys=True)
    print()


if __name__ == "__main__":
    main(*sys.argv[1:])
'''

AGG_FIX = '''"""Aggregate events.csv per event type. See README.md for the spec."""
import csv
import json
import sys
from datetime import datetime, timezone


def main(path="data/events.csv"):
    out = {}
    seen = set()
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row["event_id"] in seen:
                continue
            seen.add(row["event_id"])
            ts = datetime.fromisoformat(row["ts"]).astimezone(timezone.utc)
            d = out.setdefault(row["event"].strip().lower(), {"users": set(), "amount": 0.0, "days": set()})
            d["users"].add(row["user"])
            d["amount"] += float(row["amount"].lstrip("$"))
            d["days"].add(ts.date().isoformat())
    result = {k: {"users": len(v["users"]), "amount": round(v["amount"], 2), "days": len(v["days"])}
              for k, v in sorted(out.items())}
    json.dump(result, sys.stdout, indent=1, sort_keys=True)
    print()


if __name__ == "__main__":
    main(*sys.argv[1:])
'''


def data_aggregate(seed: int) -> Task:
    rnd = random.Random(seed)

    def setup(repo: Path, hidden: Path):
        events = ["view", "click", "purchase", "refund", "signup"]
        offsets = ["+00:00", "+02:00", "-05:00", "+09:00", "-08:00"]
        rows = []
        t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
        for i in range(24_000):
            t = t0 + timedelta(seconds=rnd.randint(0, 20 * 86400))
            off = rnd.choice(offsets)
            sign, hh, mm = off[0], int(off[1:3]), int(off[4:6])
            delta = timedelta(hours=hh, minutes=mm) * (1 if sign == "+" else -1)
            local = (t + delta).replace(tzinfo=None).isoformat() + off
            amount = rnd.random() * 100 if rnd.random() < 0.6 else 0
            ev = rnd.choice(events)
            rows.append({"event_id": "ev%07d" % i, "ts": local, "user": "u%04d" % rnd.randint(1, 1500),
                         "event": ev.capitalize() if rnd.random() < 0.15 else ev,
                         "amount": ("$%.2f" % amount) if rnd.random() < 0.2 else "%.2f" % amount})
        dups = rnd.sample(rows, 1_500)  # exact duplicate rows, as a flaky exporter would produce
        rows = rows + dups
        rnd.shuffle(rows)
        (repo / "data").mkdir(parents=True, exist_ok=True)
        with open(repo / "data" / "events.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["event_id", "ts", "user", "event", "amount"])
            w.writeheader()
            w.writerows(rows)
        _w(repo / "aggregate.py", AGG_PY)
        _w(repo / "README.md", "# events report\n\n`python3 aggregate.py > out.json` must print, per event type, sorted by type:\n\n"
           "- `users`: number of distinct users\n- `amount`: total amount, rounded to 2 decimals\n- `days`: number of distinct "
           "**UTC** calendar days the event type occurred on (timestamps in the CSV carry their local offset)\n\n"
           "Rows with the same `event_id` are the same event exported twice and must be counted once. Event names are "
           "case-insensitive (`Click` is `click`). Some amounts carry a `$` prefix.\n\n"
           "The current output is wrong.\n")
        # expected, from the reference implementation
        _w(hidden / "ref.py", AGG_FIX)
        r = subprocess.run([PY, str(hidden / "ref.py"), str(repo / "data" / "events.csv")], capture_output=True, text=True)
        _w(hidden / "expected.json", r.stdout)

    def grade(repo: Path, hidden: Path) -> Dict:
        exp = json.loads((hidden / "expected.json").read_text())
        r = _run([PY, "aggregate.py"], repo)
        try:
            got = json.loads(r.stdout)
        except ValueError:
            return {"score": 0.0, "error": "no JSON on stdout"}
        ok = sum(1 for k, v in exp.items() if got.get(k) == v)
        return {"score": round(ok / len(exp), 3), "types_correct": "{}/{}".format(ok, len(exp))}

    def solve(repo: Path, hidden: Path):
        _w(repo / "aggregate.py", AGG_FIX)

    return Task("data-aggregate", "aggregate.py produces wrong numbers for data/events.csv; README.md has the spec. Fix "
                "aggregate.py so `python3 aggregate.py` prints the correct report (JSON on stdout, nothing else).",
                setup, grade, solve, seed, ["shell-output", "data"])


# --------------------------------------------------------------- 6 deprecate-walk

UTILS_PY = '''"""Shared helpers."""
import hashlib


def legacy_hash(s):
    """DEPRECATED: md5 is not stable across our data pipeline. Use stable_hash."""
    return hashlib.md5(s.encode()).hexdigest()[:8]


def stable_hash(s, *, length=12):
    return hashlib.sha256(s.encode()).hexdigest()[:length]
'''

UTILS_FIX = '''"""Shared helpers."""
import hashlib


def stable_hash(s, *, length=12):
    return hashlib.sha256(s.encode()).hexdigest()[:length]
'''

DEP_FORMS = [
    ("from app.utils import legacy_hash\n\n\ndef key_{i}(name):\n    return 'k-' + legacy_hash(name)\n",
     "from app.utils import stable_hash\n\n\ndef key_{i}(name):\n    return 'k-' + stable_hash(name, length=16)\n"),
    ("from app import utils\n\n\ndef key_{i}(name):\n    return utils.legacy_hash(name.lower())\n",
     "from app import utils\n\n\ndef key_{i}(name):\n    return utils.stable_hash(name.lower(), length=16)\n"),
    ("import app.utils as u\n\n\ndef key_{i}(name):\n    parts = [u.legacy_hash(p) for p in name.split('/')]\n    return '/'.join(parts)\n",
     "import app.utils as u\n\n\ndef key_{i}(name):\n    parts = [u.stable_hash(p, length=16) for p in name.split('/')]\n    return '/'.join(parts)\n"),
]
DEP_DECOYS = [
    "from app.utils import stable_hash\n\n# NOTE: legacy_hash was used here before 2024; kept for the record.\n\n\ndef key_{i}(name):\n    return stable_hash(name, length=16)\n",
    "from app.utils import stable_hash\n\nDOC = \"legacy_hash is deprecated; this module never used it\"\n\n\ndef key_{i}(name):\n    return stable_hash(name, length=16)\n",
]


def deprecate_walk(seed: int) -> Task:
    rnd = random.Random(seed)
    n = 60
    kinds = [rnd.choice(["call", "call", "decoy", "clean"]) for _ in range(n)]
    forms = [rnd.randrange(3) for _ in range(n)]

    def setup(repo: Path, hidden: Path):
        _w(repo / "app" / "__init__.py", "")
        _w(repo / "app" / "utils.py", UTILS_PY)
        imports, body = [], []
        for i in range(n):
            mod = "mod%02d" % i
            if kinds[i] == "call":
                src = DEP_FORMS[forms[i]][0].format(i=i)
            elif kinds[i] == "decoy":
                src = DEP_DECOYS[forms[i] % 2].format(i=i)
            else:
                src = "from app.utils import stable_hash\n\n\ndef key_{i}(name):\n    return stable_hash(name, length=16)\n".format(i=i)
            _w(repo / "app" / "mods" / (mod + ".py"), src)
            imports.append("from app.mods.{} import key_{}\n".format(mod, i))
            sample = "alpha/beta" if forms[i] == 2 else "Alpha"
            if kinds[i] == "call" and forms[i] == 1:
                exp = "stable_hash('alpha', length=16)"
            elif forms[i] == 2 and kinds[i] == "call":
                exp = "'/'.join(stable_hash(p, length=16) for p in 'alpha/beta'.split('/'))"
            elif kinds[i] == "call":
                exp = "'k-' + stable_hash('Alpha', length=16)"
            else:
                exp = "stable_hash(%r, length=16)" % sample
            body.append("    def test_{}(self):\n        self.assertEqual(key_{}({!r}), {})\n\n".format(mod, i, sample, exp))
        _w(repo / "app" / "mods" / "__init__.py", "")
        _w(hidden / "tests" / "__init__.py", "")
        _w(hidden / "tests" / "test_mods_hidden.py", "import unittest\nfrom app.utils import stable_hash\n" + "".join(imports) +
           "\n\nclass TestMods(unittest.TestCase):\n" + "".join(body) +
           "    def test_removed(self):\n        import app.utils\n        self.assertFalse(hasattr(app.utils, 'legacy_hash'))\n")
        _w(repo / "tests" / "__init__.py", "")
        _w(repo / "tests" / "test_utils.py", "import unittest\nfrom app.utils import stable_hash\n\n\nclass T(unittest.TestCase):\n"
           "    def test_stable(self):\n        self.assertEqual(len(stable_hash('x')), 12)\n")
        _w(repo / "CHANGELOG.md", "# Changelog\n\n## 1.2.0\n\n- Added stable_hash.\n")
        _w(repo / "README.md", "# keys\n\nTests: `python3 -m unittest discover -s tests -t . -v`\n")

    def grade(repo: Path, hidden: Path) -> Dict:
        work = hidden / "grade"
        subprocess.run(["rm", "-rf", str(work)])
        _copy_tree(repo, work)
        subprocess.run(["rm", "-rf", str(work / "tests")])
        _copy_tree(hidden / "tests", work / "tests")
        res = _unittest(work)
        tests = res["passed"] / res["total"] if res["total"] else 0.0
        changelog = "legacy_hash" in (repo / "CHANGELOG.md").read_text() if (repo / "CHANGELOG.md").exists() else False
        return {"score": round(0.9 * tests + 0.1 * changelog, 3), "hidden_tests": "{}/{}".format(res["passed"], res["total"]),
                "changelog_ok": changelog}

    def solve(repo: Path, hidden: Path):
        _w(repo / "app" / "utils.py", UTILS_FIX)
        for i in range(n):
            if kinds[i] == "call":
                _w(repo / "app" / "mods" / ("mod%02d.py" % i), DEP_FORMS[forms[i]][1].format(i=i))
        p = repo / "CHANGELOG.md"
        p.write_text(p.read_text() + "\n## 1.3.0\n\n- Removed legacy_hash; all call sites use stable_hash(length=16).\n")

    return Task("deprecate-walk", "`legacy_hash` in app/utils.py is deprecated. Migrate every real call site under app/ to "
                "`stable_hash(<same argument>, length=16)` (some modules only mention legacy_hash in comments or strings; "
                "leave those alone), then remove `legacy_hash` from app/utils.py and add a CHANGELOG.md entry that mentions "
                "the removal. `python3 -m unittest discover -s tests -t .` must pass.",
                setup, grade, solve, seed, ["chains", "edits"])


TASKS = {
    "noisy-tests": noisy_tests,
    "log-hunt": log_hunt,
    "rename-api": rename_api,
    "build-error": build_error,
    "data-aggregate": data_aggregate,
    "deprecate-walk": deprecate_walk,
}


def make(name: str, seed: int) -> Task:
    return TASKS[name](seed)


def digest(path: Path) -> str:
    h = hashlib.sha1()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]
