"""Shared HTTP client and pass/fail recorder for the verification suite.

Standard library only -- no pip install needed to run any module in this
package or scripts/verify_all.py.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://streakfit.pro"


class ApiClient:
    """Thin JSON/HTTP wrapper over urllib."""

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    def request(self, method, path, token=None, body=None):
        url = self.base_url + path
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                status = resp.status
                raw = resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            raw = e.read()
        except urllib.error.URLError as e:
            raise RuntimeError(f"network error calling {method} {path}: {e}") from e

        if not raw:
            return status, {}
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, {"_raw": raw.decode(errors="replace")}


class WsgiClient:
    """Same .request() shape as ApiClient, but dispatches through Flask's
    test client -- an in-process WSGI call, not a real socket. Used when
    verification is triggered from inside the app itself (StreakFit
    Control's "Run Verification"), so a single-worker deployment can't
    deadlock a request waiting to answer its own self-referential call.
    See scripts/verify_all.py's module docstring for the full rationale."""

    def __init__(self, flask_app):
        self._test_client = flask_app.test_client()
        self.base_url = "wsgi://in-process"

    def request(self, method, path, token=None, body=None):
        headers = {}
        if token:
            headers["Authorization"] = "Bearer " + token
        response = self._test_client.open(path, method=method, json=body, headers=headers)
        data = response.get_json(silent=True)
        return response.status_code, (data if data is not None else {})


class Results:
    """Records each check as it runs, prints it immediately, and renders
    the final pass/fail table. A `fatal` stops the run early only for
    setup failures nothing downstream could survive (e.g. can't register)."""

    def __init__(self):
        self.rows = []  # (name, passed, detail)

    def check(self, name, condition, detail=""):
        passed = bool(condition)
        self.rows.append((name, passed, detail))
        mark = "PASS" if passed else "FAIL"
        suffix = f"  — {detail}" if (detail and not passed) else ""
        print(f"  {mark:4s}  {name}{suffix}")
        return passed

    def fatal(self, message):
        print(f"\nFATAL: {message}")
        self.print_table()
        sys.exit(2)

    def all_passed(self):
        return all(ok for _, ok, _ in self.rows)

    def to_dict(self):
        """Plain-JSON-serializable summary -- used by StreakFit Control to
        hand results straight to the frontend and to VerificationRun.results_json,
        no stdout-parsing required."""
        total = len(self.rows)
        passed = sum(1 for _, ok, _ in self.rows if ok)
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "checks": [{"name": name, "passed": ok, "detail": detail} for name, ok, detail in self.rows],
        }

    def print_table(self, title=None):
        total = len(self.rows)
        passed = sum(1 for _, ok, _ in self.rows if ok)
        failed = total - passed
        print("\n" + "-" * 60)
        if title:
            print(title)
        print(f"{passed} passed, {failed} failed, {total} total")
        print("-" * 60)
        if failed:
            print("\nFailed checks:")
            for name, ok, detail in self.rows:
                if not ok:
                    print(f"  - {name}: {detail}")


def resolve_base_url(positional, flag, env_value):
    """One consistent precedence order for every module's CLI entry point:
    positional arg > --base-url flag > $SMOKE_BASE_URL > production default."""
    return positional or flag or env_value or DEFAULT_BASE_URL


def run_module_standalone(description, build_scenario, run_checks):
    """Shared `if __name__ == "__main__"` body every subsystem module uses,
    so the argparse/exit-code boilerplate exists in exactly one place.

    build_scenario(api, results) -> scenario   -- this module's own minimal
        setup when run on its own (e.g. chat.py registers users + creates
        a team; auth.py just registers users).
    run_checks(api, results, scenario) -> scenario -- the module's `run()`.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("base_url_positional", nargs="?", default=None, metavar="BASE_URL")
    parser.add_argument("--base-url", default=None)
    args = parser.parse_args()

    api = ApiClient(
        resolve_base_url(args.base_url_positional, args.base_url, os.environ.get("SMOKE_BASE_URL"))
    )
    results = Results()
    print(f"{description}\nTarget: {api.base_url}\n")

    scenario = build_scenario(api, results)
    run_checks(api, results, scenario)

    results.print_table()
    sys.exit(0 if results.all_passed() else 1)
