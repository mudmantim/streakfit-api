"""The cron specification in render.yaml, checked rather than admired.

render.yaml is INERT -- Render does not read it for this service, which was
created in the dashboard. So these blocks are a recipe a human retypes, and
a recipe with a mistake in it is worse than no recipe: it looks authoritative
and it is wrong in the one place nobody re-derives.

Three mistakes would be expensive and none would be obvious:

  * a cron that runs `flask db upgrade` -- migrations firing on a schedule,
    from a service nobody is watching, outside any deploy;
  * a cron that references a command this build does not have, which is
    exactly the state production is in today (fa92abd registers none);
  * a cron missing an environment variable its command needs, which fails
    quietly as "nothing to do" rather than loudly as "cannot connect".

Parsed as text on purpose: PyYAML is not a declared dependency of this
project, and a test that needs an undeclared package is a test that stops
running the day somebody builds a clean environment.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RENDER_YAML = ROOT / "render.yaml"


def blocks():
    """[(type, name, {key: value}, [env var names])] for each service."""
    text = RENDER_YAML.read_text(encoding="utf-8")
    out, cur = [], None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip() if not raw.strip().startswith("#") else ""
        m = re.match(r"^  - type:\s*(\S+)", line)
        if m:
            cur = {"type": m.group(1), "fields": {}, "env": []}
            out.append(cur)
            continue
        if cur is None:
            continue
        m = re.match(r"^    (\w+):\s*(.*)$", line)
        if m and m.group(1) != "envVars":
            cur["fields"][m.group(1)] = m.group(2).strip()
        m = re.match(r"^      - key:\s*(\S+)", line)
        if m:
            cur["env"].append(m.group(1))
    return out


def crons():
    return [b for b in blocks() if b["type"] == "cron"]


def web():
    return next(b for b in blocks() if b["type"] == "web")


def cli_commands():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    return set(re.findall(r'@app\.cli\.command\("([^"]+)"\)', src))


def test_the_file_declares_the_services_we_expect():
    names = {b["fields"].get("name") for b in blocks()}
    assert "streakfit-api" in names
    assert len(crons()) >= 1, "no cron specified at all"


# ── A cron must never migrate ───────────────────────────────────────────────

@pytest.mark.parametrize("field", ["startCommand", "buildCommand"])
def test_no_cron_can_run_a_migration(field):
    for c in crons():
        cmd = c["fields"].get(field, "")
        assert "db upgrade" not in cmd, f"{c['fields']['name']} migrates in {field}"
        assert "db downgrade" not in cmd
        assert "db stamp" not in cmd


def test_only_the_web_service_migrates():
    """One place runs migrations, and it is the deploy."""
    assert "db upgrade" in web()["fields"]["startCommand"]


# ── A cron must not send, or claim to have sent, during setup ───────────────

def test_the_delivery_cron_records_itself_as_scheduled():
    notify = [c for c in crons() if "moderation-notify" in c["fields"].get("startCommand", "")]
    assert notify, "no delivery cron specified"
    for c in notify:
        assert "--scheduled" in c["fields"]["startCommand"], (
            "without --scheduled the run records as 'manual' and deliberately "
            "never satisfies moderation.delivery_worker")


def test_no_cron_marks_notices_delivered_by_fiat():
    """`--mark-delivered` claims a human was told. A cron is not a human."""
    for c in crons():
        assert "--mark-delivered" not in c["fields"].get("startCommand", "")


def test_no_cron_starts_the_in_process_sweeper():
    """A one-shot job that also spawns an hourly thread would deliver twice
    from one process and keep the job alive doing it."""
    for c in crons():
        assert "STREAKFIT_RETENTION_SWEEPER" not in c["env"], c["fields"]["name"]
        assert "STREAKFIT_RETENTION_SWEEPER" not in c["fields"].get("startCommand", "")


# ── A cron must be able to reach the things it needs ────────────────────────

def test_every_cron_can_reach_the_database():
    for c in crons():
        for required in ("DATABASE_URL", "SECRET_KEY", "JWT_SECRET_KEY"):
            assert required in c["env"], \
                f"{c['fields']['name']} is missing {required}"


def test_the_delivery_cron_carries_the_whole_channel_or_none_of_it():
    """Half a channel is the state most likely to be believed: the job runs,
    finds a queue, and can send none of it."""
    for c in crons():
        if "moderation-notify" not in c["fields"].get("startCommand", ""):
            continue
        for required in ("STREAKFIT_NOTIFY_CHANNEL", "RESEND_API_KEY",
                         "STREAKFIT_NOTIFY_FROM", "STREAKFIT_NOTIFY_TO"):
            assert required in c["env"], \
                f"delivery cron is missing {required}"
        assert "STREAKFIT_PUBLIC_URL" in c["env"], \
            "without it the notice says 'the moderation queue' instead of a link"


def test_the_retention_cron_can_decrypt_what_it_purges():
    for c in crons():
        if "moderation-prune" not in c["fields"].get("startCommand", ""):
            continue
        assert "STREAKFIT_EVIDENCE_KEY" in c["env"], (
            "photo evidence cannot be purged without the key configured; a "
            "DIFFERENT key here would leave images undecryptable")


# ── A cron must reference commands that exist ───────────────────────────────

def test_every_cron_command_exists_in_this_build():
    """The production state today: fa92abd registers no CLI commands at all,
    so a cron created now would fail every run."""
    available = cli_commands()
    assert available, "no CLI commands found in app.py at all"

    for c in crons():
        for token in re.findall(r"flask\s+([a-z-]+)", c["fields"].get("startCommand", "")):
            assert token in available, (
                f"{c['fields']['name']} runs `flask {token}`, which this build "
                f"does not define (has: {sorted(available)})")


# ── A cron must match the service it shares a database with ─────────────────

def test_crons_match_the_web_service_region_and_branch():
    w = web()["fields"]
    for c in crons():
        assert c["fields"].get("region") == w.get("region"), \
            f"{c['fields']['name']} is in a different region from the database"
        assert c["fields"].get("branch") == w.get("branch"), \
            f"{c['fields']['name']} deploys a different branch"


def test_every_cron_installs_the_dependencies_it_runs_on():
    for c in crons():
        assert "requirements.txt" in c["fields"].get("buildCommand", ""), \
            c["fields"]["name"]


def test_every_cron_has_a_schedule():
    for c in crons():
        sched = c["fields"].get("schedule", "").strip('"\'')
        assert re.match(r"^[\d*/,\- ]+$", sched), \
            f"{c['fields']['name']} has no usable schedule: {sched!r}"
        assert len(sched.split()) == 5, sched


def test_the_delivery_cron_runs_at_least_hourly():
    """A 24-hour review clock is not well served by a daily check."""
    for c in crons():
        if "moderation-notify" not in c["fields"].get("startCommand", ""):
            continue
        minute, hour = c["fields"]["schedule"].strip('"\'').split()[:2]
        assert hour == "*", f"delivery runs only at hour {hour}; it must be hourly"
