#!/usr/bin/env python3
"""Which Render identity variables does THIS phase of a deploy see?

    python scripts/render_env_probe.py pre-deploy && flask db upgrade
    python scripts/render_env_probe.py runtime && STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app

Written for staging step S0.7 (docs/operations/staging-pw1-test-plan.md). It
answers the one open question that gates the guard release: db_guard rule R2
lets the production database be used only by a process that can show
production's RENDER_SERVICE_ID. Render documents that variable for build and
runtime; Pre-Deploy is not mentioned. If it is missing there, production's own
`flask db upgrade` would be refused.

Prints only:
  * the PRESENCE of the variables below, and the value of the non-secret ones
    (Render's own identifiers and flags);
  * the Neon endpoint ID named by DATABASE_URL, never the URL itself;
  * the verdict for that question, computed by db_guard's own policy code.

It never fails the command it precedes (exit 0), and it imports nothing from
the app. It does not connect to anything.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db_guard  # noqa: E402

SHOWN = ('RENDER', 'RENDER_SERVICE_ID', 'RENDER_SERVICE_NAME', 'RENDER_SERVICE_TYPE', 'RENDER_INSTANCE_ID',
         'RENDER_GIT_COMMIT', 'RENDER_GIT_BRANCH', 'IS_PULL_REQUEST', 'STREAKFIT_DEPLOYMENT',
         'STREAKFIT_ENFORCE_DB_HEAD', 'STREAKFIT_RETENTION_SWEEPER')
PRESENCE_ONLY = ('DATABASE_URL', 'MIGRATION_DATABASE_URL', 'STREAKFIT_PRODUCTION_DB_ENDPOINT',
                 'STREAKFIT_ALLOW_PRODUCTION_DB_FROM_HERE')


def report(phase: str, env=os.environ) -> list:
    lines = [f'render_env_probe phase={phase}']
    for key in SHOWN:
        value = env.get(key)
        lines.append(f'  {key}={value}' if value else f'  {key}: MISSING')
    for key in PRESENCE_ONLY:
        lines.append(f'  {key}: {"set" if env.get(key) else "MISSING"}')
    lines.append(f'  DATABASE_URL endpoint={db_guard.neon_endpoint(env.get("DATABASE_URL") or "") or "none"}')

    # The question: if this phase belonged to PRODUCTION's service, would R2
    # accept the production database? Ask db_guard itself, with production's
    # endpoint and this phase's own environment. Only the service ID decides
    # it, so substitute production's ID only if this phase HAS one at all.
    prod_ep = next(iter(db_guard.PRODUCTION_DB_ENDPOINTS))
    prod_id = next(iter(db_guard.PRODUCTION_RENDER_SERVICE_IDS))
    has_id = bool((env.get('RENDER_SERVICE_ID') or '').strip())
    hypothetical = {k: v for k, v in env.items() if k != 'STREAKFIT_DEPLOYMENT'}
    hypothetical['RENDER_SERVICE_ID'] = prod_id if has_id else ''
    hosted = db_guard._is_hosted(hypothetical)
    try:
        db_guard._check_policy(prod_ep, 'production', hosted, prod_id if has_id else None, hypothetical,
                               declared=False)
        verdict = 'PASS (production would be accepted in this phase)'
    except db_guard.DatabaseBindingError as exc:
        verdict = f'FAIL ({str(exc).split(":", 1)[0]}: production would be REFUSED in this phase)'
    lines.append(f'  hosted={hosted} production-in-this-phase: {verdict}')
    return lines


def main(argv: list) -> int:
    phase = argv[0] if argv else 'unspecified'
    try:
        print('\n'.join(report(phase)), flush=True)
    except Exception as exc:     # a probe must never block the deploy step it precedes
        print(f'render_env_probe phase={phase} error={type(exc).__name__}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
