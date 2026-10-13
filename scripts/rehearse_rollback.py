#!/usr/bin/env python3
"""Rehearse a rollback between two builds on a DISPOSABLE local PostgreSQL.

    python scripts/rehearse_rollback.py <deployed ref> <target ref> <server url> [--json]

<server url> must name a PostgreSQL server on localhost / 127.0.0.1 / ::1
(a throwaway container). The tool refuses anything else -- it creates a
scratch database there and drops it at the end, and it starts real app
processes against it.

Production's sequence, reproduced step by step (each step a separate process,
from a clean git worktree of that ref, so nothing is shared but the database):

  1. DEPLOYED build: Pre-Deploy (`flask db upgrade`) on an empty database.
  2. DEPLOYED build boots with STREAKFIT_ENFORCE_DB_HEAD=1 (production's start
     flags) and serves; its own verify_all runs against it. This leaves real
     rows behind, written by the newer code.
  3. ROLLBACK: the TARGET build's Pre-Deploy (`flask db upgrade`) runs against
     that database -- exactly what Render does on a rollback deploy.
  4. TARGET build boots with the head check on. If it boots, its own
     verify_all runs against the data the deployed build wrote.

Verdicts:
  SAFE        target Pre-Deploy passes, it boots, its verify_all passes.
  BLOCKED     target Pre-Deploy or boot refuses (Render would abort the deploy
              and keep the deployed build serving: safe, but this is not a
              rollback path -- use a compatibility build or a downgrade).
  UNSAFE      target boots but its verify_all fails on the deployed build's data.
  BROKEN      the deployed build itself does not migrate/boot/verify here.

Exit 0 for SAFE, 1 otherwise. scripts/rollback_check.py answers the schema
question statically in a second; this answers "does it actually work".
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}
BOOT_TIMEOUT_S = 90
VERIFY_TIMEOUT_S = 1800
TAIL = 1500


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _tail(text: str) -> str:
    return (text or '')[-TAIL:]


class Rehearsal:
    def __init__(self, deployed: str, target: str, server_url: str):
        from sqlalchemy.engine import make_url
        self.server = make_url(server_url)
        if (self.server.host or '').lower() not in LOCAL_HOSTS:
            raise SystemExit(f'refusing: {self.server.host!r} is not a local disposable server '
                             f'({", ".join(sorted(LOCAL_HOSTS))})')
        self.refs = {'deployed': deployed, 'target': target}
        self.repo = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True,
                                   text=True, check=True).stdout.strip()
        self.work = tempfile.mkdtemp(prefix='sf-rollback-')
        self.trees = {}
        self.db = f'sf_rb_{uuid.uuid4().hex[:8]}'
        self.report = {'deployed': deployed, 'target': target, 'steps': []}

    # -- setup / teardown ----------------------------------------------------
    def __enter__(self):
        from sqlalchemy import create_engine, text
        for role, ref in self.refs.items():
            path = os.path.join(self.work, role)
            subprocess.run(['git', '-C', self.repo, 'worktree', 'add', '--detach', '-q', path, ref],
                           check=True, capture_output=True)
            sha = subprocess.run(['git', '-C', path, 'rev-parse', 'HEAD'], capture_output=True,
                                 text=True, check=True).stdout.strip()
            self.trees[role] = path
            self.report[f'{role}_sha'] = sha
        self.admin = create_engine(self.server.set(database='postgres'), isolation_level='AUTOCOMMIT')
        with self.admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{self.db}"'))
        self.url = self.server.set(database=self.db).render_as_string(hide_password=False)
        return self

    def __exit__(self, *exc):
        from sqlalchemy import text
        try:
            with self.admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{self.db}" WITH (FORCE)'))
            self.admin.dispose()
        finally:
            for path in self.trees.values():
                subprocess.run(['git', '-C', self.repo, 'worktree', 'remove', '--force', path],
                               capture_output=True)
            shutil.rmtree(self.work, ignore_errors=True)

    # -- steps -----------------------------------------------------------------
    def _env(self, boot: bool) -> dict:
        env = {k: v for k, v in os.environ.items()
               if k not in ('RENDER', 'RENDER_SERVICE_ID', 'STREAKFIT_DEPLOYMENT', 'MIGRATION_DATABASE_URL',
                            'STREAKFIT_RETENTION_SWEEPER', 'STREAKFIT_ENFORCE_DB_HEAD', 'SMOKE_BASE_URL',
                            'RATELIMIT_STORAGE_URI', 'ANTHROPIC_API_KEY')}
        env.update(DATABASE_URL=self.url, FLASK_APP='app', SECRET_KEY='rollback-rehearsal',
                   JWT_SECRET_KEY='rollback-rehearsal-jwt-0123456789abcdef01234567',
                   ADMIN_SECRET='rollback-rehearsal-admin', PYTHONDONTWRITEBYTECODE='1')
        if boot:
            env['STREAKFIT_ENFORCE_DB_HEAD'] = '1'     # production's boot guard, ON
        return env

    def _step(self, name: str, ok: bool, **detail) -> bool:
        self.report['steps'].append({'step': name, 'ok': ok, **detail})
        return ok

    def predeploy(self, role: str) -> bool:
        p = subprocess.run([sys.executable, '-m', 'flask', 'db', 'upgrade'], cwd=self.trees[role],
                           env=self._env(boot=False), capture_output=True, text=True, timeout=600)
        return self._step(f'{role}: pre-deploy (flask db upgrade)', p.returncode == 0,
                          exit=p.returncode, output=_tail(p.stderr + p.stdout))

    def serve_and_verify(self, role: str) -> tuple:
        """(booted, verified). Boots the build like production and runs its own
        verify_all against it."""
        port = _free_port()
        base = f'http://127.0.0.1:{port}'
        log = open(os.path.join(self.work, f'{role}-server.log'), 'w+')
        server = subprocess.Popen([sys.executable, '-m', 'flask', 'run', '--port', str(port), '--no-reload'],
                                  cwd=self.trees[role], env=self._env(boot=True), stdout=log,
                                  stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + BOOT_TIMEOUT_S
            up = False
            while time.monotonic() < deadline and server.poll() is None:
                try:
                    with urllib.request.urlopen(base + '/health', timeout=2) as r:
                        up = r.status == 200
                        break
                except Exception:  # noqa: BLE001 -- not up yet
                    time.sleep(0.5)
            log.flush()
            log.seek(0)
            if not up:
                return self._step(f'{role}: boot with head check', False, exit=server.poll(),
                                  output=_tail(log.read())), False
            self._step(f'{role}: boot with head check', True)
            # verify_all's URL is ALWAYS explicit: its default is production.
            v = subprocess.run([sys.executable, 'scripts/verify_all.py', base], cwd=self.trees[role],
                               env=self._env(boot=False), capture_output=True, text=True,
                               timeout=VERIFY_TIMEOUT_S)
            summary = [ln for ln in v.stdout.splitlines() if ' passed, ' in ln and ' failed' in ln]
            return True, self._step(f'{role}: verify_all against {base}', v.returncode == 0,
                                    summary=summary[-1] if summary else None,
                                    output=None if v.returncode == 0 else _tail(v.stdout + v.stderr))
        finally:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
            log.close()

    def run(self) -> str:
        if not self.predeploy('deployed'):
            return 'BROKEN'
        booted, verified = self.serve_and_verify('deployed')
        if not (booted and verified):
            return 'BROKEN'
        if not self.predeploy('target'):
            return 'BLOCKED'
        booted, verified = self.serve_and_verify('target')
        if not booted:
            return 'BLOCKED'
        return 'SAFE' if verified else 'UNSAFE'


def main(argv: list) -> int:
    as_json = '--json' in argv
    args = [a for a in argv if a != '--json']
    if len(args) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    with Rehearsal(*args) as r:
        verdict = r.run()
        r.report['verdict'] = verdict
    if as_json:
        print(json.dumps(r.report, indent=2))
    else:
        print(f"deployed {r.report['deployed']} ({r.report['deployed_sha'][:12]}) -> "
              f"target {r.report['target']} ({r.report['target_sha'][:12]})")
        for s in r.report['steps']:
            line = f"  {'PASS' if s['ok'] else 'FAIL'}  {s['step']}"
            if s.get('summary'):
                line += f"  [{s['summary'].strip()}]"
            print(line)
            if not s['ok'] and s.get('output'):
                print('        ' + s['output'].strip().replace('\n', '\n        ')[-800:])
        print(f'VERDICT: {verdict}')
    return 0 if verdict == 'SAFE' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
