#!/usr/bin/env python3
"""Production build gate for StreakFit.

StreakFit has no bundler — `static/` is served as-is, so there is no compile
step that would fail on a broken reference. That is exactly why this exists:
without it, a renamed icon or a typo'd asset path ships silently and shows up
as a broken image in a user's browser. This script is the substitute for the
build a bundled app would get.

What it checks (all offline, no network, no database):

  1. Every local asset referenced by index.html, app.js, style.css, sw.js and
     manifest.json exists on disk. This is the broken-image gate.
  2. sw.js precache list is internally consistent — every entry exists, and the
     cache name carries a version so a deploy actually invalidates old caches.
  3. static/*.js parses (node --check), so a syntax error cannot reach users.
     Skipped with a warning if node is unavailable.
  4. manifest.json and every shipped JSON data file parse.
  5. app.py imports cleanly under production-shaped env vars.
  6. Every exercise in EXERCISE_LIBRARY has the illustration the API promises.
     The mission API hands the client `/static/exercises/{key}.svg`, so these
     references live in Python and are invisible to the static-file scan above —
     a renamed or missing SVG would reach a user as a broken image in the
     exercise modal.

Run:  make build-check     (or: python scripts/build_check.py)
Exit 0 = shippable, 1 = do not deploy.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

failures: list[str] = []
warnings: list[str] = []
checks_run = 0


def fail(msg: str) -> None:
    failures.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def resolve(ref: str) -> Path | None:
    """Map a URL reference to a repo path, or None if it isn't a local asset."""
    ref = ref.strip().strip("'\"")
    if not ref or ref.startswith(("http://", "https://", "//", "data:", "#", "mailto:")):
        return None
    ref = ref.split("?", 1)[0].split("#", 1)[0]
    if ref.startswith("/static/"):
        return STATIC / ref[len("/static/"):]
    return None


# ── 1 + 2. Asset references resolve to real files ────────────────────────────
def check_asset_references() -> None:
    global checks_run
    # (label, file, regex capturing candidate refs)
    sources = [
        ("index.html", STATIC / "index.html", r"""(?:src|href)\s*=\s*["']([^"']+)["']"""),
        ("app.js", STATIC / "app.js", r"""["'](/static/[^"']+)["']"""),
        ("style.css", STATIC / "style.css", r"""url\(\s*['"]?([^'")]+)"""),
        ("sw.js", STATIC / "sw.js", r"""["'](/static/[^"']+)["']"""),
    ]
    for label, path, pattern in sources:
        if not path.exists():
            fail(f"{label}: expected file {path} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        for ref in sorted(set(re.findall(pattern, text))):
            target = resolve(ref)
            if target is None:
                continue
            checks_run += 1
            if not target.exists():
                fail(f"{label}: references {ref} — no such file ({target.relative_to(ROOT)})")

    manifest = STATIC / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(f"manifest.json: invalid JSON — {exc}")
        else:
            for icon in data.get("icons", []):
                target = resolve(icon.get("src", ""))
                if target is None:
                    continue
                checks_run += 1
                if not target.exists():
                    fail(f"manifest.json: icon {icon['src']} — no such file")
            for key in ("start_url", "scope"):
                if not data.get(key):
                    fail(f"manifest.json: missing required key {key!r} (PWA install breaks)")


def check_service_worker() -> None:
    global checks_run
    sw = STATIC / "sw.js"
    if not sw.exists():
        fail("sw.js is missing — offline shell and cache busting are gone")
        return
    text = sw.read_text(encoding="utf-8")

    checks_run += 1
    cache_name = re.search(r"""const\s+CACHE\s*=\s*['"]([^'"]+)['"]""", text)
    if not cache_name:
        fail("sw.js: no `const CACHE = '...'` — cannot verify cache versioning")
    elif not re.search(r"v\d+", cache_name.group(1)):
        fail(
            f"sw.js: cache name {cache_name.group(1)!r} has no version token (expected e.g. "
            "'streakfit-v0747'); without one a deploy cannot invalidate stale caches"
        )

    checks_run += 1
    static_list = re.search(r"const\s+STATIC\s*=\s*\[(.*?)\]", text, re.S)
    if not static_list:
        fail("sw.js: no STATIC precache array found")
    else:
        entries = re.findall(r"""['"]([^'"]+)['"]""", static_list.group(1))
        if not entries:
            fail("sw.js: STATIC precache array is empty")
        # addAll() rejects atomically: ONE missing entry silently kills install
        # for the whole app, so every entry matters.
        for entry in entries:
            target = resolve(entry)
            if target is not None and not target.exists():
                fail(f"sw.js: precache entry {entry} does not exist — caches.addAll() would reject")


# ── 3. JavaScript parses ─────────────────────────────────────────────────────
def check_js_syntax() -> None:
    global checks_run
    node = shutil.which("node")
    js_files = sorted(STATIC.glob("*.js"))
    if not js_files:
        fail("static/ contains no .js files")
        return
    if not node:
        warn(f"node not found — skipped syntax check of {len(js_files)} JS file(s)")
        return
    for js in js_files:
        checks_run += 1
        proc = subprocess.run(
            [node, "--check", str(js)], capture_output=True, text=True, timeout=60, check=False
        )
        if proc.returncode != 0:
            fail(f"{js.name}: JavaScript syntax error — {proc.stderr.strip().splitlines()[:3]}")


# ── 4. Shipped JSON parses ───────────────────────────────────────────────────
def check_json_files() -> None:
    global checks_run
    for path in sorted(ROOT.glob("*.json")) + sorted(STATIC.glob("*.json")):
        checks_run += 1
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(f"{path.relative_to(ROOT)}: invalid JSON — {exc}")


# ── 5. The app imports under production-shaped env ───────────────────────────
def check_app_imports() -> None:
    global checks_run
    checks_run += 1
    env = dict(os.environ)
    env.update(
        SECRET_KEY="build-check-not-for-production",
        JWT_SECRET_KEY="build-check-not-for-production",
        # A file-backed SQLite URL keeps import side effects off any real DB.
        DATABASE_URL="sqlite:///build_check_import_only.db",
    )
    # Must NOT be set: it would make the import assert Alembic head and exit.
    env.pop("STREAKFIT_ENFORCE_DB_HEAD", None)
    proc = subprocess.run(
        [sys.executable, "-c", "import app; assert app.app"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180, check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        fail("app.py does not import cleanly:\n    " + "\n    ".join(tail))
    (ROOT / "build_check_import_only.db").unlink(missing_ok=True)
    (ROOT / "instance" / "build_check_import_only.db").unlink(missing_ok=True)


# ── 6. Server-generated exercise illustrations exist ─────────────────────────
def check_exercise_illustrations() -> None:
    """The API promises /static/exercises/{key}.svg for every exercise it serves.

    Run in a subprocess so importing app.py cannot leave state behind in this
    process, and so a missing dependency shows up as one clear failure.
    """
    global checks_run
    checks_run += 1
    probe = (
        "import json, os, sys;"
        "import app;"
        "keys=sorted({e['key'] for pools in app.EXERCISE_LIBRARY.values()"
        " for exs in pools.values() for e in exs});"
        "print(json.dumps(keys))"
    )
    env = dict(os.environ)
    env.update(
        SECRET_KEY="build-check-not-for-production",
        JWT_SECRET_KEY="build-check-not-for-production",
        DATABASE_URL="sqlite:///build_check_exercises_only.db",
    )
    env.pop("STREAKFIT_ENFORCE_DB_HEAD", None)
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180, check=False,
    )
    (ROOT / "build_check_exercises_only.db").unlink(missing_ok=True)
    (ROOT / "instance" / "build_check_exercises_only.db").unlink(missing_ok=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-4:]
        fail("could not read EXERCISE_LIBRARY:\n    " + "\n    ".join(tail))
        return
    try:
        keys = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        fail(f"could not parse exercise keys from probe output — {exc}")
        return
    if not keys:
        fail("EXERCISE_LIBRARY yielded zero exercise keys — the check would pass vacuously")
        return
    missing = [k for k in keys if not (STATIC / "exercises" / f"{k}.svg").exists()]
    if missing:
        shown = ", ".join(missing[:10]) + (f" (+{len(missing) - 10} more)" if len(missing) > 10 else "")
        fail(
            f"{len(missing)} of {len(keys)} exercises have no illustration at "
            f"static/exercises/<key>.svg — the exercise modal would show a broken image: {shown}"
        )


def main() -> int:
    for check in (
        check_asset_references,
        check_service_worker,
        check_js_syntax,
        check_json_files,
        check_app_imports,
        check_exercise_illustrations,
    ):
        try:
            check()
        except Exception as exc:  # a crashing check must not read as a pass
            fail(f"{check.__name__} crashed: {type(exc).__name__}: {exc}")

    for w in warnings:
        print(f"WARN  {w}")
    if failures:
        print(f"\nBUILD CHECK FAILED — {len(failures)} problem(s):\n")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print(f"BUILD CHECK PASSED — {checks_run} assertions, 0 problems.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
