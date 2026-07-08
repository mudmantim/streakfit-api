#!/usr/bin/env python3
"""StreakFit R2 Social Layer — production-safe smoke test.

Automates the manual R2.1-R2.8 verification pass (auth, mission, teams,
campfire, moments, chat, Rickie's team reactions, invite rotation, remove
member, leave team, unauthorized access) as a single script that can be
re-run any time against any environment, most importantly production.

Safe to run against production:
  - Only ever creates new `qa_smoke_*` throwaway accounts (see
    qa_test_accounts memory precedent) -- never touches an existing user.
  - Only ever creates one new team, named `Smoke Test <run tag>`, and only
    acts on that team -- never touches any pre-existing team.
  - Every request is a normal authenticated API call any real user could
    make; nothing here needs direct database access, an admin secret, or
    any route not already reachable from the app itself.
  - There is no account-deletion endpoint in the API (a known, accepted
    limitation -- see qa_test_accounts memory), so the throwaway accounts
    and the team persist as empty rows after the run, same as every prior
    manual QA pass. That's expected, not a bug in this script.
  - Does not modify app.py or any other application file -- script only.

Usage:
    python3 scripts/smoke_r2_social.py
    python3 scripts/smoke_r2_social.py --base-url https://streakfit.pro
    SMOKE_BASE_URL=http://localhost:5000 python3 scripts/smoke_r2_social.py

Exit code is 0 if every check passed, 1 if any check failed, 2 if a
fatal setup step failed (e.g. couldn't even register a user) and the
remaining checks were skipped because nothing downstream could possibly
have worked without it.

Requires only the Python 3 standard library -- no pip install needed.
"""
import argparse
import json
import os
import random
import string
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://streakfit.pro"
SMOKE_PASSWORD = "SmokeTest123!"


def _rand_suffix(n=6):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


class ApiClient:
    """Thin JSON/HTTP wrapper over urllib -- stdlib only, no dependencies."""

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


class Results:
    """Records each check as it runs, prints it immediately, and renders
    the final pass/fail table. A `fatal` stops the run early only for
    setup failures nothing downstream could survive (e.g. can't register)."""

    def __init__(self):
        self.rows = []

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

    def print_table(self):
        total = len(self.rows)
        passed = sum(1 for _, ok, _ in self.rows if ok)
        failed = total - passed
        print("\n" + "-" * 60)
        print(f"{passed} passed, {failed} failed, {total} total")
        print("-" * 60)
        if failed:
            print("\nFailed checks:")
            for name, ok, detail in self.rows:
                if not ok:
                    print(f"  - {name}: {detail}")


def main():
    parser = argparse.ArgumentParser(
        description="StreakFit R2 social-layer production-safe smoke test"
    )
    parser.add_argument(
        "base_url_positional",
        nargs="?",
        default=None,
        metavar="BASE_URL",
        help="API base URL, positional form (e.g. https://streakfit.pro)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"API base URL, flag form (default: {DEFAULT_BASE_URL}, or $SMOKE_BASE_URL)",
    )
    args = parser.parse_args()

    base_url = (
        args.base_url_positional
        or args.base_url
        or os.environ.get("SMOKE_BASE_URL")
        or DEFAULT_BASE_URL
    )
    api = ApiClient(base_url)
    r = Results()
    run_tag = f"{int(time.time())}_{_rand_suffix()}"

    print("StreakFit R2 Social Layer — Smoke Test")
    print(f"Target:  {api.base_url}")
    print(f"Run tag: {run_tag}\n")

    # ── health ──────────────────────────────────────────────────────────
    status, _ = api.request("GET", "/health")
    r.check("health.ok", status == 200, f"status={status}")

    # ── auth: register + log in four throwaway qa_smoke_* accounts ──────
    users = {}
    for role in ("a", "b", "c", "outsider"):
        username = f"qa_smoke_{role}_{run_tag}"
        status, _ = api.request(
            "POST", "/api/register",
            body={"username": username, "password": SMOKE_PASSWORD},
        )
        if not r.check(f"auth.register_{role}", status == 201, f"status={status}"):
            r.fatal(f"could not register {username} — nothing downstream can run without accounts")

        status, data = api.request(
            "POST", "/api/login",
            body={"username": username, "password": SMOKE_PASSWORD},
        )
        ok = r.check(
            f"auth.login_{role}",
            status == 200 and "access_token" in data,
            f"status={status}",
        )
        if not ok:
            r.fatal(f"could not log in {username} — nothing downstream can run without a token")
        users[role] = {"username": username, "token": data["access_token"]}

    tok_a = users["a"]["token"]
    tok_b = users["b"]["token"]
    tok_c = users["c"]["token"]
    tok_out = users["outsider"]["token"]

    # ── teams: create + join ─────────────────────────────────────────────
    # Done before the mission below -- the campfire/first-log/Rickie-
    # reaction logic only fires for teams a user is *already* a member of
    # at the moment they complete a mission, so the team has to exist and
    # A has to have joined it first.
    team_name = f"Smoke Test {run_tag}"
    status, team_resp = api.request(
        "POST", "/api/teams", token=tok_a, body={"name": team_name}
    )
    if not r.check("teams.create", status == 201 and "team" in team_resp, f"status={status}"):
        r.fatal("could not create team — everything downstream needs one")

    team = team_resp["team"]
    team_id = team["id"]
    code_1 = team["invite_code"]
    r.check(
        "teams.invite_code_format",
        isinstance(code_1, str) and len(code_1) == 6,
        f"code={code_1!r}",
    )

    status, _ = api.request(
        "POST", f"/api/teams/{team_id}/join", token=tok_b, body={"code": code_1}
    )
    r.check("teams.member_b_joins", status == 200, f"status={status}")

    status, detail = api.request("GET", f"/api/teams/{team_id}", token=tok_a)
    ok = r.check(
        "teams.roster_shows_two_members",
        status == 200 and detail.get("member_count") == 2,
        f"status={status} member_count={detail.get('member_count')}",
    )
    if ok:
        roster_usernames = {m["username"] for m in detail["members"]}
        r.check(
            "teams.roster_lists_both_by_name",
            {users["a"]["username"], users["b"]["username"]} <= roster_usernames,
            f"roster={roster_usernames}",
        )

    # ── mission: complete today's daily mission as A, now a team member ─
    status, daily = api.request("GET", "/api/daily", token=tok_a)
    ok = r.check("mission.get_daily", status == 200 and "exercises" in daily, f"status={status}")
    if ok:
        keys = [ex["key"] for ex in daily["exercises"]]
        r.check("mission.five_exercises_today", len(keys) == 5, f"got {len(keys)}")
        last_status = None
        for key in keys:
            last_status, _ = api.request("POST", f"/api/daily/{key}/complete", token=tok_a)
        r.check("mission.complete_all_five", last_status == 200, f"last status={last_status}")

    # ── campfire: the mission completion above should have logged once ──
    status, campfire = api.request("GET", f"/api/teams/{team_id}/campfire", token=tok_a)
    r.check(
        "campfire.one_log_recorded",
        status == 200 and campfire.get("total_team_missions") == 1,
        f"status={status} total={campfire.get('total_team_missions')}",
    )
    r.check(
        "campfire.stage_is_kindling",
        campfire.get("stage") == "Kindling",
        f"stage={campfire.get('stage')}",
    )

    # ── moments: durable history for team_created / member_joined / log ─
    status, moments = api.request("GET", f"/api/teams/{team_id}/moments", token=tok_a)
    ok = r.check("moments.readable", status == 200, f"status={status}")
    if ok:
        moment_types = {m["moment_type"] for m in moments}
        r.check("moments.team_created_present", "team_created" in moment_types)
        r.check("moments.member_joined_present", "member_joined" in moment_types)
        r.check("moments.campfire_log_added_present", "campfire_log_added" in moment_types)

    # ── Rickie's team reactions (ordinary team_message rows) ────────────
    # RICKIE_TEAM_MESSAGES (app.py) picks randomly from a small template
    # pool per trigger, so these check for exact membership in the known
    # pool rather than a substring that would only match one variant.
    WELCOME_TEMPLATES = {"Welcome to the campfire.", "Glad you're here."}
    FIRST_LOG_TEMPLATES = {"First log added. The fire is starting."}

    status, messages = api.request("GET", f"/api/teams/{team_id}/messages", token=tok_a)
    ok = r.check("chat.readable", status == 200, f"status={status}")
    if ok:
        rickie_bodies = [
            m["body"] for m in messages if m.get("sender_type") == "rickie"
        ]
        r.check(
            "rickie.welcome_message_on_join",
            any(b in WELCOME_TEMPLATES for b in rickie_bodies),
            f"rickie said: {rickie_bodies}",
        )
        r.check(
            "rickie.first_log_message",
            any(b in FIRST_LOG_TEMPLATES for b in rickie_bodies),
            f"rickie said: {rickie_bodies}",
        )

    # ── chat: a real message posted by A is readable by B ───────────────
    chat_body = f"smoke test message {run_tag}"
    status, posted = api.request(
        "POST", f"/api/teams/{team_id}/messages", token=tok_a, body={"body": chat_body}
    )
    r.check(
        "chat.post_as_a",
        status == 201 and posted.get("body") == chat_body,
        f"status={status}",
    )

    status, messages_b = api.request("GET", f"/api/teams/{team_id}/messages", token=tok_b)
    ok = r.check("chat.read_as_b", status == 200, f"status={status}")
    if ok:
        r.check(
            "chat.b_sees_a_message",
            any(m["body"] == chat_body for m in messages_b),
        )

    # ── invite rotation: old code stops working, new code works ─────────
    status, rotate_resp = api.request(
        "POST", f"/api/teams/{team_id}/rotate-invite", token=tok_a
    )
    ok = r.check(
        "invite.rotate_as_creator",
        status == 200 and "invite_code" in rotate_resp,
        f"status={status}",
    )
    code_2 = rotate_resp.get("invite_code")

    if ok:
        status, _ = api.request(
            "POST", f"/api/teams/{team_id}/join", token=tok_c, body={"code": code_1}
        )
        r.check("invite.old_code_rejected", status == 403, f"status={status}")

        status, _ = api.request(
            "POST", f"/api/teams/{team_id}/join", token=tok_c, body={"code": code_2}
        )
        r.check("invite.new_code_accepted", status == 200, f"status={status}")

    # ── remove member: creator removes C, who loses access immediately ──
    status, me_c = api.request("GET", "/api/me", token=tok_c)
    c_user_id = me_c.get("id")
    ok = r.check("remove.fetch_c_user_id", status == 200 and c_user_id is not None, f"status={status}")

    if ok:
        status, _ = api.request(
            "DELETE", f"/api/teams/{team_id}/members/{c_user_id}", token=tok_a
        )
        r.check("remove.creator_removes_c", status == 200, f"status={status}")

        status, _ = api.request("GET", f"/api/teams/{team_id}/messages", token=tok_c)
        r.check("remove.c_loses_chat_access", status == 403, f"status={status}")

        status, _ = api.request("GET", f"/api/teams/{team_id}/moments", token=tok_c)
        r.check("remove.c_loses_moments_access", status == 403, f"status={status}")

        status, _ = api.request("GET", f"/api/teams/{team_id}", token=tok_c)
        r.check("remove.c_loses_team_detail_access", status == 403, f"status={status}")

    # ── leave team: B leaves voluntarily and disappears from B's list ───
    status, _ = api.request("POST", f"/api/teams/{team_id}/leave", token=tok_b)
    r.check("leave.b_leaves", status == 200, f"status={status}")

    status, teams_b = api.request("GET", "/api/teams", token=tok_b)
    ok = r.check("leave.b_team_list_readable", status == 200, f"status={status}")
    if ok:
        r.check(
            "leave.team_gone_from_b_list",
            all(t["id"] != team_id for t in teams_b),
        )

    # ── unauthorized access: outsider and no-token requests are blocked ─
    status, _ = api.request("GET", f"/api/teams/{team_id}/messages", token=tok_out)
    r.check("unauthorized.outsider_chat_read_blocked", status == 403, f"status={status}")

    status, _ = api.request(
        "POST", f"/api/teams/{team_id}/messages", token=tok_out, body={"body": "nope"}
    )
    r.check("unauthorized.outsider_chat_post_blocked", status == 403, f"status={status}")

    status, _ = api.request("GET", f"/api/teams/{team_id}/moments", token=tok_out)
    r.check("unauthorized.outsider_moments_blocked", status == 403, f"status={status}")

    status, _ = api.request("GET", f"/api/teams/{team_id}", token=tok_out)
    r.check("unauthorized.outsider_team_detail_blocked", status == 403, f"status={status}")

    status, _ = api.request("GET", "/api/daily")  # no Authorization header at all
    r.check("unauthorized.no_token_blocked", status == 401, f"status={status}")

    r.print_table()
    sys.exit(0 if r.all_passed() else 1)


if __name__ == "__main__":
    main()
