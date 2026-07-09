#!/usr/bin/env python3
"""Admin / StreakFit Control subsystem — confirms the Mission Control
routes (R3.0) exist, are reachable, and reject unauthenticated requests.

Deliberately does NOT trigger POST /api/admin/verify (that would start a
real verification run recursively from inside a verification run) and
does NOT test the "valid secret" path -- this suite has no business
knowing the real ADMIN_SECRET value, and shouldn't have it hardcoded
into a checked-in script. "Missing secret" and "wrong secret" hit the
identical rejection branch in app.py's _require_admin_secret, so
checking the former covers the real security property either way."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone

_SECRET_GATED_ROUTES = {
    "admin.stats_blocked_without_secret": "/api/admin/stats",
    "admin.project_status_blocked_without_secret": "/api/admin/project-status",
    "admin.system_health_blocked_without_secret": "/api/admin/system-health",
    "admin.verify_status_blocked_without_secret": "/api/admin/verify/status",
    "admin.verify_history_blocked_without_secret": "/api/admin/verify/history",
}


def run(api, results, scenario):
    status, _ = api.request("GET", "/admin")
    results.check("admin.dashboard_page_reachable", status == 200, f"status={status}")

    for check_name, path in _SECRET_GATED_ROUTES.items():
        status, _ = api.request("GET", path)
        results.check(check_name, status == 403, f"status={status}")

    # Wrong HTTP method on the verify-trigger route is rejected, not
    # silently accepted (and never reaches the secret check at all).
    status, _ = api.request("GET", "/api/admin/verify")
    results.check("admin.verify_trigger_wrong_method_rejected", status == 405, f"status={status}")

    return scenario


def _build_scenario(api, results):
    return None  # this module needs no team/user setup


if __name__ == "__main__":
    run_module_standalone("Admin / StreakFit Control subsystem verification", _build_scenario, run)
