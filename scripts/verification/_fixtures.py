"""Shared scenario-building blocks for the verification suite.

Every subsystem module needs some baseline state to check against --
throwaway users, a team, sometimes a completed mission. These helpers
build that state once so `verify_all.py` doesn't repeat it eight times,
while still letting each module build its own minimal scenario when run
standalone during development.

Only ever creates new `qa_smoke_*` accounts and a disposable
`Smoke Test <run tag>` team -- never touches an existing user or team.
See qa_test_accounts memory for the established throwaway-account
convention this follows.
"""
import random
import string
import time

SMOKE_PASSWORD = "SmokeTest123!"
ROLES = ("a", "b", "c", "outsider")


def new_run_tag():
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{int(time.time())}_{suffix}"


class Scenario:
    """Mutable bag of state a run builds up as modules execute against it.
    Not a dataclass on purpose -- fields get added incrementally (users
    first, then team_id/invite_code once teams.run() creates the team,
    etc.) rather than all existing up front."""

    def __init__(self, api, run_tag):
        self.api = api
        self.run_tag = run_tag
        self.users = {}  # role -> {"username": str, "token": str, "id": int|None}
        self.team_id = None
        self.team_name = None
        self.invite_code = None


def register_and_login_users(api, results, run_tag, roles=ROLES):
    """Registers + logs in each role as qa_smoke_<role>_<run_tag>. Fatal
    (aborts the whole run) if any account can't be created or logged in
    -- nothing downstream can work without real tokens."""
    users = {}
    for role in roles:
        username = f"qa_smoke_{role}_{run_tag}"
        status, _ = api.request(
            "POST", "/api/register",
            body={"username": username, "password": SMOKE_PASSWORD},
        )
        if not results.check(f"auth.register_{role}", status == 201, f"status={status}"):
            results.fatal(f"could not register {username} — nothing downstream can run without accounts")

        status, data = api.request(
            "POST", "/api/login",
            body={"username": username, "password": SMOKE_PASSWORD},
        )
        ok = results.check(
            f"auth.login_{role}",
            status == 200 and "access_token" in data,
            f"status={status}",
        )
        if not ok:
            results.fatal(f"could not log in {username} — nothing downstream can run without a token")
        users[role] = {"username": username, "token": data["access_token"], "id": None}
    return users


def fetch_user_id(api, results, users, role, check_name=None):
    """Fills in users[role]["id"] via /api/me. Several modules (remove-
    member, in particular) need a user's numeric id, which register/login
    don't return."""
    status, me = api.request("GET", "/api/me", token=users[role]["token"])
    ok = results.check(
        check_name or f"auth.fetch_{role}_user_id",
        status == 200 and me.get("id") is not None,
        f"status={status}",
    )
    if ok:
        users[role]["id"] = me["id"]
    return ok


def create_and_join_team(api, results, scenario, creator_role="a", joiner_roles=("b",)):
    """Creates a team as `creator_role`, joins it as each of `joiner_roles`.
    Mutates and returns `scenario` with team_id/team_name/invite_code set."""
    users = scenario.users
    team_name = f"Smoke Test {scenario.run_tag}"
    status, team_resp = api.request(
        "POST", "/api/teams", token=users[creator_role]["token"], body={"name": team_name}
    )
    if not results.check("teams.create", status == 201 and "team" in team_resp, f"status={status}"):
        results.fatal("could not create team — everything downstream needs one")

    team = team_resp["team"]
    scenario.team_id = team["id"]
    scenario.team_name = team_name
    scenario.invite_code = team["invite_code"]
    results.check(
        "teams.invite_code_format",
        isinstance(scenario.invite_code, str) and len(scenario.invite_code) == 6,
        f"code={scenario.invite_code!r}",
    )

    for role in joiner_roles:
        status, _ = api.request(
            "POST", f"/api/teams/{scenario.team_id}/join",
            token=users[role]["token"], body={"code": scenario.invite_code},
        )
        results.check(f"teams.member_{role}_joins", status == 200, f"status={status}")

    return scenario


def complete_daily_mission(api, results, token, check_prefix="mission"):
    """Completes all of today's exercises for the given token. Returns
    True only if every step succeeded."""
    status, daily = api.request("GET", "/api/daily", token=token)
    ok = results.check(f"{check_prefix}.get_daily", status == 200 and "exercises" in daily, f"status={status}")
    if not ok:
        return False

    keys = [ex["key"] for ex in daily["exercises"]]
    results.check(f"{check_prefix}.five_exercises_today", len(keys) == 5, f"got {len(keys)}")

    last_status = None
    for key in keys:
        last_status, _ = api.request("POST", f"/api/daily/{key}/complete", token=token)
    return results.check(f"{check_prefix}.complete_all_five", last_status == 200, f"last status={last_status}")


def build_team_scenario(api, results, roles=ROLES, joiner_roles=("b",)):
    """The common case: register all roles, create a team as 'a', join
    as 'b' (and any other joiner_roles). What most modules need when run
    standalone. Does NOT complete a mission -- callers that need campfire/
    moments/rickie-reaction state call complete_daily_mission() themselves,
    since not every module needs it."""
    run_tag = new_run_tag()
    scenario = Scenario(api, run_tag)
    scenario.users = register_and_login_users(api, results, run_tag, roles=roles)
    create_and_join_team(api, results, scenario, creator_role="a", joiner_roles=joiner_roles)
    return scenario
