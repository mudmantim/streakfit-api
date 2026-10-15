#!/usr/bin/env python3
"""Rehearse the RC-B staging rehearsal locally, with no hosted service at all.

    python3 scripts/simulate_staging_rehearsal.py --tree <release tree> --commit <sha> \\
        [--rollback-tree <older tree>] [--checker scripts/staging_log_check.py] [--verify-all]

What it builds (all disposable, removed at the end):
  * a podman network created with --internal: no route off this machine, so
    nothing in it can reach Neon, Render or anything else, by construction;
  * one local PostgreSQL 17 container (the same image digest as CI). Each
    scenario gets a fresh database whose `neon.endpoint_id` setting is the
    endpoint the server should claim, so db_guard's R7 server check runs for
    real;
  * app containers that mount the release tree READ-ONLY and the same Python
    venv the local checks use, with /etc/hosts entries mapping fabricated
    `ep-...neon.tech` names (direct and -pooler) to that PostgreSQL.

Each scenario runs Render's two phases with Render-shaped variables
(fabricated service ID, the release commit and branch):
  Pre-Deploy: python scripts/render_env_probe.py pre-deploy && flask db upgrade
  Start:      python scripts/render_env_probe.py runtime && STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app
and collects the real log. Where the scenario corresponds to a rehearsal step,
the log is judged by the rehearsal log checker, so the checker is tested
against real app output, not only synthetic fixtures.

Every credential is fabricated. What this does NOT prove is printed at the end.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

PG_IMAGE = 'docker.io/library/postgres:17@sha256:5f050f770b427fbd477edee6c3968a72e5c6be97e050a7e368b2b74a9494a285'
EP = 'ep-fake-staging-00000000'
OTHER_EP = 'ep-fake-other-11111111'
PROD_EP = 'ep-odd-star-ajye3lag'                     # only ever inside a refused, unresolvable URL
HOST = f'{EP}.c-2.us-east-2.aws.neon.tech'
POOLER = f'{EP}-pooler.c-2.us-east-2.aws.neon.tech'
SID = 'srv-fakestaging000000000'
PROD_SID = 'srv-d8f1hh6rnols73ak3a10'
BRANCH = 'rc/safety-b'
PG_PASSWORD = 'sim-not-a-secret'
SUBNET, PG_IP = '10.89.77.0/24', '10.89.77.10'
HEAD = 'd4f1b6e2a9c3'


def sh(*cmd, check=True, **kw):
    return subprocess.run(list(cmd), capture_output=True, text=True, check=check, **kw)


class Sim:
    def __init__(self, a):
        self.a = a
        self.tag = uuid.uuid4().hex[:8]
        self.net = f'sf-sim-{self.tag}'
        self.pg = f'sf-sim-pg-{self.tag}'
        self.venv = Path(a.venv).resolve()
        self.base_python = Path(os.path.realpath(self.venv / 'bin' / 'python')).parents[1]
        self.results = []
        self.out = Path(a.out)
        self.out.mkdir(parents=True, exist_ok=True)

    # -- infrastructure -------------------------------------------------------
    def up(self):
        sh('podman', 'network', 'create', '--internal', '--subnet', SUBNET, self.net)
        sh('podman', 'run', '-d', '--rm', '--name', self.pg, '--network', self.net, '--ip', PG_IP,
           '-e', f'POSTGRES_PASSWORD={PG_PASSWORD}', '-e', 'POSTGRES_USER=neondb_owner', PG_IMAGE)
        for _ in range(60):
            if sh('podman', 'exec', self.pg, 'pg_isready', '-U', 'neondb_owner', '-q', check=False).returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError('local PostgreSQL did not start')

    def down(self):
        for name in sh('podman', 'ps', '-a', '--format', '{{.Names}}', check=False).stdout.split():
            if name.startswith(f'sf-sim-app-{self.tag}') or name == self.pg:
                sh('podman', 'rm', '-f', name, check=False)
        sh('podman', 'network', 'rm', '-f', self.net, check=False)

    def fresh_db(self, name, server_endpoint=EP):
        q = [f'create database {name}']
        if server_endpoint:
            q.append(f"alter database {name} set neon.endpoint_id = '{server_endpoint}'")
        for stmt in q:
            sh('podman', 'exec', self.pg, 'psql', '-U', 'neondb_owner', '-d', 'postgres', '-qc', stmt)

    def url(self, db, host=HOST):
        return f'postgresql://neondb_owner:{PG_PASSWORD}@{host}:5432/{db}?sslmode=disable'

    # -- one Render phase -------------------------------------------------------
    def _run_args(self, tree, env, name, detach=False):
        args = ['podman', 'run', '--rm' if not detach else '-d', '--name', name, '--network', self.net,
                '--add-host', f'{HOST}:{PG_IP}', '--add-host', f'{POOLER}:{PG_IP}',
                '-v', f'{tree}:/app:ro', '-v', f'{self.venv}:{self.venv}:ro',
                '-v', f'{self.base_python}:{self.base_python}:ro', '-w', '/app', '--entrypoint', 'sh',
                # SELinux: read the bind mounts without relabelling the host's files (":z" would
                # permanently change their context). Only these disposable containers are affected.
                '--security-opt', 'label=disable']
        base = {'PATH': f'{self.venv}/bin:/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1', 'FLASK_APP': 'app',
                'PYTHONUNBUFFERED': '1', 'SECRET_KEY': 'sim-secret-not-real',
                'JWT_SECRET_KEY': 'sim-jwt-not-real-0123456789abcdef0123456789', 'ADMIN_SECRET': 'sim-admin-not-real',
                'STREAKFIT_AGE_POLICY': 'off'}
        for k, v in {**base, **env}.items():
            if v is not None:
                args += ['-e', f'{k}={v}']
        return args + [PG_IMAGE]      # the app runs in the same Debian image as the database (has sh)

    def render_env(self, db=None, url=None, sid=SID, deployment='staging', commit=None, branch=BRANCH, **extra):
        env = {'RENDER': 'true', 'RENDER_SERVICE_ID': sid, 'RENDER_SERVICE_NAME': 'streakfit-staging-sim',
               'RENDER_SERVICE_TYPE': 'web', 'RENDER_GIT_COMMIT': commit or self.a.commit,
               'RENDER_GIT_BRANCH': branch, 'IS_PULL_REQUEST': 'false', 'STREAKFIT_DEPLOYMENT': deployment,
               'DATABASE_URL': url if url is not None else (self.url(db) if db else None)}
        env.update(extra)
        return {k: v for k, v in env.items() if v not in (None, '')}

    def predeploy(self, tree, env, probe=True):
        cmd = ('python scripts/render_env_probe.py pre-deploy && ' if probe else '') + 'python -m flask db upgrade'
        name = f'sf-sim-app-{self.tag}-pre-{uuid.uuid4().hex[:4]}'
        p = sh(*self._run_args(tree, env, name), '-c', cmd, check=False, timeout=600)
        return p.returncode, (p.stdout + p.stderr).splitlines()

    def boot(self, tree, env, probe=True, sweeper=False, wait_s=60):
        """Start like Render; returns (healthy, log lines, build-identity or None, container name)."""
        flags = 'STREAKFIT_ENFORCE_DB_HEAD=1 ' + ('STREAKFIT_RETENTION_SWEEPER=1 ' if sweeper else '')
        cmd = (('python scripts/render_env_probe.py runtime && ' if probe else '')
               + f'{flags}exec gunicorn app:app -b 127.0.0.1:8000 --workers 1 --timeout 120')
        name = f'sf-sim-app-{self.tag}-run-{uuid.uuid4().hex[:4]}'
        run_env = dict(env)
        if probe:   # the probe reports the flags too, exactly as the Start Command sets them on Render
            run_env['STREAKFIT_ENFORCE_DB_HEAD'] = '1'
            if sweeper:
                run_env['STREAKFIT_RETENTION_SWEEPER'] = '1'
        sh(*self._run_args(tree, run_env, name, detach=True), '-c', cmd)
        healthy, ident = False, None
        fetch = ("import json,urllib.request as u;"
                 "print(json.dumps(json.load(u.urlopen('http://127.0.0.1:8000/api/build-identity',timeout=3))))")
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            r = sh('podman', 'exec', name, 'python', '-c',
                   "import urllib.request as u;print(u.urlopen('http://127.0.0.1:8000/health',timeout=3).status)",
                   check=False)
            if r.returncode == 0 and r.stdout.strip() == '200':
                healthy = True
                got = sh('podman', 'exec', name, 'python', '-c', fetch, check=False)
                ident = json.loads(got.stdout) if got.returncode == 0 else None
                break
            if sh('podman', 'inspect', '-f', '{{.State.Running}}', name, check=False).stdout.strip() != 'true':
                break
            time.sleep(1)
        return healthy, name, ident

    def logs_and_stop(self, name):
        logs = sh('podman', 'logs', name, check=False)
        sh('podman', 'rm', '-f', name, check=False)
        return (logs.stdout + logs.stderr).splitlines()

    def verify_all(self, name):
        p = sh('podman', 'exec', name, 'python', 'scripts/verify_all.py', 'http://127.0.0.1:8000',
               check=False, timeout=1500)
        summary = [ln for ln in p.stdout.splitlines() if ' passed, ' in ln and ' failed' in ln]
        return p.returncode, (summary[-1].strip() if summary else 'no summary')

    # -- judging ------------------------------------------------------------------
    def checker(self, mode, log_lines, scenario):
        path = self.out / f'{scenario}.log'
        path.write_text('\n'.join(log_lines) + '\n')
        args = [sys.executable, self.a.checker, mode, str(path), '--service', SID, '--commit', self.a.commit,
                '--branch', BRANCH]
        if mode == 'deploy':
            args += ['--endpoint', EP, '--head', HEAD]
        p = sh(*args, check=False)
        fails = [ln.split()[1] for ln in p.stdout.splitlines() if ln.startswith('FAIL')]
        return p.returncode, fails

    def record(self, scenario, expectation, ok, **detail):
        self.results.append({'scenario': scenario, 'expected': expectation, 'ok': bool(ok), **detail})
        print(f'{"PASS" if ok else "FAIL"}  {scenario}: {expectation}  {json.dumps(detail, sort_keys=True)}',
              flush=True)

    # -- scenarios ----------------------------------------------------------------
    def deploy(self, scenario, db, url=None, sweeper=False, check_mode='deploy', verify=False, **env_over):
        env = self.render_env(db=db, url=url, **env_over)
        rc, pre = self.predeploy(self.a.tree, env)
        if rc != 0:
            return rc, pre, None, None, (None, None)
        healthy, name, ident = self.boot(self.a.tree, env, sweeper=sweeper)
        verified = self.verify_all(name) if (verify and healthy) else None
        run = self.logs_and_stop(name)
        return rc, pre, run, healthy, (ident, verified)

    def s0_isolation(self):
        """The network really has no way out: any name outside it fails to resolve or connect."""
        probe = ("import socket\n"
                 "for h in ('ep-odd-star-ajye3lag.c-2.us-east-2.aws.neon.tech', 'api.render.com', 'example.com'):\n"
                 "    try:\n"
                 "        socket.create_connection((h, 443), timeout=5); print('REACHED', h)\n"
                 "    except OSError:\n"
                 "        print('blocked', h)\n")
        name = f'sf-sim-app-{self.tag}-iso'
        p = sh(*self._run_args(self.a.tree, {}, name), '-c', f'python -c "{probe}"', check=False, timeout=120)
        lines = p.stdout.split('\n')
        self.record('S0 network isolation', 'production Neon host, Render API and the internet are unreachable',
                    p.returncode == 0 and lines.count('REACHED') == 0 and sum(ln.startswith('blocked') for ln in lines) == 3,
                    output=[ln for ln in lines if ln])

    def s1_good(self):
        self.fresh_db('s1')
        rc, pre, run, healthy, (ident, verified) = self.deploy('s1', 's1', verify=self.a.verify_all)
        log = pre + (run or [])
        crc, fails = self.checker('deploy', log, 's1_good_direct')
        sweeper_started = any('event=retention_sweeper_started' in ln for ln in log)
        self.record('S1 good deploy, direct string', 'migrates, boots, checker PASS, sweeper off, identity staging',
                    rc == 0 and healthy and crc == 0 and not sweeper_started and ident
                    and ident.get('environment') == 'staging' and ident.get('gitSha') == self.a.commit[:12]
                    and ident.get('gitBranch') == BRANCH and (verified is None or verified[0] == 0),
                    predeploy_exit=rc, healthy=healthy, checker_exit=crc, checker_fails=fails,
                    build_identity={k: (ident or {}).get(k) for k in ('environment', 'gitSha', 'gitBranch')},
                    sweeper_started=sweeper_started, verify_all=verified[1] if verified else 'not run')

    def s2_pooled(self):
        self.fresh_db('s2')
        rc, pre, run, healthy, _ = self.deploy('s2', 's2', url=self.url('s2', host=POOLER))
        crc, fails = self.checker('deploy', pre + (run or []), 's2_pooled')
        self.record('S2 pooled-shaped host (-pooler name, same local server)',
                    'endpoint normalised, checker PASS', rc == 0 and healthy and crc == 0,
                    predeploy_exit=rc, healthy=healthy, checker_exit=crc, checker_fails=fails)

    def s3_r2(self):
        prod_url = f'postgresql://nobody:not-a-password@{PROD_EP}.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require'
        rc, pre, *_ = self.deploy('s3', None, url=prod_url)
        crc, fails = self.checker('r2', pre, 's3_r2_replay')
        self.record('S3 R2 replay: production-shaped URL, fabricated password',
                    'Pre-Deploy refused R2 before any connection, checker r2 PASS', rc != 0 and crc == 0,
                    predeploy_exit=rc, checker_exit=crc, checker_fails=fails)

    def s3b_r2_without_sid(self):
        prod_url = f'postgresql://nobody:not-a-password@{PROD_EP}.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require'
        rc, pre, *_ = self.deploy('s3b', None, url=prod_url, sid='')
        refused = [ln for ln in pre if 'Refusing to start:' in ln]
        crc, fails = self.checker('r2', pre, 's3b_r2_without_sid')
        self.record('S3b R2 replay where Pre-Deploy LACKS RENDER_SERVICE_ID',
                    'still refused (R3), but the checker FAILS the replay (hides Q-A otherwise)',
                    rc != 0 and any('R3:' in ln for ln in refused) and crc == 1,
                    predeploy_exit=rc, checker_exit=crc, checker_fails=fails)

    def s4_r1(self):
        rc, pre, *_ = self.deploy('s4', None, url='')
        crc, fails = self.checker('r1', pre, 's4_r1_replay')
        self.record('S4 R1 replay: DATABASE_URL removed', 'refused R1, checker r1 PASS', rc != 0 and crc == 0,
                    predeploy_exit=rc, checker_exit=crc, checker_fails=fails)

    def _refusal(self, scenario, label, rule, **kw):
        rc, pre, *_ = self.deploy(scenario, kw.pop('db', None), **kw)
        refused = [ln for ln in pre if 'Refusing to start:' in ln]
        self.record(label, f'Pre-Deploy refused {rule}, no migration',
                    rc != 0 and len(refused) == 1 and f'Refusing to start: {rule}:' in refused[0]
                    and not any('Running upgrade' in ln for ln in pre), predeploy_exit=rc,
                    rule_seen=[r.split('Refusing to start: ')[1][:2] for r in refused])

    def s5_undeclared(self):
        self.fresh_db('s5')
        self._refusal('s5', 'S5 STREAKFIT_DEPLOYMENT missing on a staging service', 'R4', db='s5', deployment='')

    def s6_wrong_service(self):
        self.fresh_db('s6')
        self._refusal('s6', 'S6 production service ID, undeclared, on the staging database', 'R4',
                      db='s6', sid=PROD_SID, deployment='')
        self.fresh_db('s6b')
        rc, pre, run, healthy, _ = self.deploy('s6b', 's6b', sid='srv-someotherservice0000')
        crc, fails = self.checker('deploy', pre + (run or []), 's6b_wrong_service')
        self.record('S6b a different (non-production) service deploys correctly',
                    'the guard accepts it, but the checker FAILS (not the expected service)',
                    rc == 0 and crc == 1 and 'PRE-SERVICE' in fails, predeploy_exit=rc, checker_fails=fails)

    def s7_wrong_endpoint(self):
        self.fresh_db('s7', server_endpoint=OTHER_EP)
        self._refusal('s7', 'S7 the server claims another Neon endpoint than the URL names (R7)', 'R7', db='s7')

    def s7b_server_silent(self):
        """The server reports no endpoint at all: R7 cannot run (documented fail-open off Neon)."""
        self.fresh_db('s7b', server_endpoint=None)
        rc, pre, run, healthy, _ = self.deploy('s7b', 's7b')
        crc, fails = self.checker('deploy', pre + (run or []), 's7b_server_silent')
        self.record('S7b the server reports NO endpoint',
                    'guard proceeds (R7 skipped), the checker FAILS MIG-SERVER/ID-STAGING',
                    rc == 0 and crc == 1 and 'MIG-SERVER' in fails, predeploy_exit=rc, healthy=healthy,
                    checker_fails=fails)

    def s8_head(self):
        self.fresh_db('s8')
        env = self.render_env(db='s8')
        healthy, name, _ = self.boot(self.a.tree, env, wait_s=30)
        log = self.logs_and_stop(name)
        refused = any('Refusing to start: database is at Alembic revision' in ln for ln in log)
        self.record('S8 start without Pre-Deploy (database not at head)',
                    'boot refuses (head check), never healthy', not healthy and refused,
                    healthy=healthy, head_refusal_logged=refused)

    def s9_sweeper(self):
        self.fresh_db('s9')
        rc, pre, run, healthy, _ = self.deploy('s9', 's9', sweeper=True)
        log = pre + (run or [])
        crc, fails = self.checker('deploy', log, 's9_sweeper_on')
        started = any('event=retention_sweeper_started' in ln for ln in log)
        self.record('S9 Start Command still has the sweeper flag',
                    'the checker FAILS RUN-NO-SWEEPER (the sweeper did start)',
                    crc == 1 and 'RUN-NO-SWEEPER' in fails and started, checker_fails=fails, sweeper_started=started)

    def s10_rollback(self):
        if not self.a.rollback_tree:
            return
        self.fresh_db('s10')
        rc, pre, run, healthy, _ = self.deploy('s10', 's10')
        env = self.render_env(db='s10', commit=self.a.rollback_commit, branch='main', deployment='staging')
        rrc, rpre = self.predeploy(self.a.rollback_tree, env, probe=False)
        rhealthy, name, ident = self.boot(self.a.rollback_tree, env, probe=False)
        self.logs_and_stop(name)
        self.record('S10 roll back: the previous build on the RC-B-migrated staging database',
                    'Pre-Deploy no-op, boots at head, healthy, reports the previous commit',
                    rc == 0 and healthy and rrc == 0 and rhealthy
                    and (ident or {}).get('gitSha') == (self.a.rollback_commit or '')[:12],
                    rc_b_ok=rc == 0 and healthy, rollback_predeploy_exit=rrc, rollback_healthy=rhealthy,
                    rollback_sha=(ident or {}).get('gitSha'))

    def run(self):
        try:
            self.up()
            for s in (self.s0_isolation, self.s1_good, self.s2_pooled, self.s3_r2, self.s3b_r2_without_sid, self.s4_r1,
                      self.s5_undeclared, self.s6_wrong_service, self.s7_wrong_endpoint, self.s7b_server_silent,
                      self.s8_head, self.s9_sweeper, self.s10_rollback):
                try:
                    s()
                except Exception as exc:  # noqa: BLE001 -- a broken scenario is a FAIL, never a skip
                    self.record(s.__name__, 'scenario ran', False, error=f'{type(exc).__name__}: {exc}'[:300])
        finally:
            self.down()
        (self.out / 'results.json').write_text(json.dumps(self.results, indent=1))
        ok = all(r['ok'] for r in self.results)
        print(f'SIMULATION: {sum(r["ok"] for r in self.results)}/{len(self.results)} scenarios as expected'
              f' -> {"ALL AS EXPECTED" if ok else "SOMETHING UNEXPECTED"}')
        print(NOT_PROVEN)
        return 0 if ok else 1


NOT_PROVEN = """NOT proven locally (needs real staging):
  * whether Render's Pre-Deploy phase actually receives RENDER_SERVICE_ID (Q-A): here it is set by hand;
  * R7 through Neon's real PgBouncer pooler: here the -pooler name reaches the same plain PostgreSQL;
  * Render's build, its gunicorn defaults, its log format and its checkout of the branch;
  * Neon's TLS, authentication and real `neon.endpoint_id` (here a database setting stands in)."""


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--tree', required=True, help='release tree to test (mounted read-only)')
    p.add_argument('--commit', required=True, help='that tree\'s full commit SHA')
    p.add_argument('--rollback-tree', help='previous release tree, for S10')
    p.add_argument('--rollback-commit', help='its full commit SHA')
    p.add_argument('--checker', default=str(Path(__file__).with_name('staging_log_check.py')))
    p.add_argument('--venv', default=str(Path(sys.executable).parents[1]))
    p.add_argument('--out', default='sim-out')
    p.add_argument('--verify-all', action='store_true', help='also run verify_all against the S1 deploy')
    a = p.parse_args(argv)
    a.tree, a.checker = str(Path(a.tree).resolve()), str(Path(a.checker).resolve())
    if a.rollback_tree:
        a.rollback_tree = str(Path(a.rollback_tree).resolve())
    return Sim(a).run()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
