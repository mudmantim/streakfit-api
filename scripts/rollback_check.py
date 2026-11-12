#!/usr/bin/env python3
"""Can we roll back from one build to another by redeploying? Answered from git
alone -- no database, no network.

    python scripts/rollback_check.py <deployed ref> <rollback target ref>

Production boots with STREAKFIT_ENFORCE_DB_HEAD=1: a build refuses to start
unless the database is at THAT build's migration head. So "just redeploy the
previous commit" works only when both builds share a head. When the deployed
build added migrations, the database is ahead of the target's head, and a
plain redeploy fails its boot check (and Render keeps serving the newer build).
Then there are exactly two ways back, and this tool says which migrations each
one involves:

  * a COMPATIBILITY build: the target's behaviour, but the deployed build's
    migration chain (head unchanged), or
  * DOWNGRADE the database to the target head first -- destructive if any
    downgrade drops a column or table, which this tool flags.

Exit 0: redeploy-safe. Exit 1: not redeploy-safe (details printed). Exit 2:
the refs do not share migration history.
"""
import re
import subprocess
import sys

VERSIONS = 'migrations/versions'
_REV = re.compile(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", re.M)
_DOWN = re.compile(r"^down_revision\s*=\s*(.+)$", re.M)
_DESTRUCTIVE = re.compile(r"\b(drop_column|drop_table|drop_index|execute\()")


def _git(*args: str) -> str:
    return subprocess.run(['git', *args], capture_output=True, text=True, check=True).stdout


def chain(ref: str) -> dict:
    """revision -> (down_revision or None, downgrade body)."""
    out = {}
    for path in _git('ls-tree', '--name-only', f'{ref}:{VERSIONS}').split():
        if not path.endswith('.py'):
            continue
        src = _git('show', f'{ref}:{VERSIONS}/{path}')
        rev = _REV.search(src)
        down = _DOWN.search(src)
        if not rev or not down:
            continue
        down_value = down.group(1).strip().strip('\'"')
        body = src.split('def downgrade', 1)[1] if 'def downgrade' in src else ''
        out[rev.group(1)] = (None if down_value in ('None', '') else down_value, body)
    return out


def head(revs: dict) -> str:
    parents = {d for d, _ in revs.values() if d}
    heads = [r for r in revs if r not in parents]
    if len(heads) != 1:
        raise SystemExit(f'expected one head, found {heads}')
    return heads[0]


def path_between(revs: dict, newer: str, older: str) -> list:
    """Revisions from newer back to (not including) older, newest first; None if
    older is not an ancestor of newer."""
    steps, cur = [], newer
    while cur and cur != older:
        steps.append(cur)
        cur = revs.get(cur, (None, ''))[0]
    return steps if cur == older else None


def main(argv: list) -> int:
    deployed_ref, target_ref = argv
    deployed, target = chain(deployed_ref), chain(target_ref)
    d_head, t_head = head(deployed), head(target)
    print(f'deployed {deployed_ref}: head {d_head}')
    print(f'target   {target_ref}: head {t_head}')
    if d_head == t_head:
        print('REDEPLOY-SAFE: both builds expect the same schema; redeploying the target needs no migration.')
        return 0
    steps = path_between(deployed, d_head, t_head)
    if steps is None:
        back = path_between(target, t_head, d_head)
        if back is not None:
            print(f'The TARGET is ahead of the deployed build by {len(back)} migration(s): {", ".join(back)}.')
            print("That is a roll-FORWARD: the target's Pre-Deploy applies them. Not a rollback concern.")
            return 0
        print('NO SHARED HISTORY: the two migration chains diverge; neither head is an ancestor of the other.')
        return 2
    print(f'NOT REDEPLOY-SAFE: the database will be at {d_head}; the target refuses to boot unless it is at {t_head}.')
    print(f'Migrations between them (newest first): {", ".join(steps)}')
    destructive = [s for s in steps if _DESTRUCTIVE.search(deployed[s][1])]
    print('Ways back:')
    print('  1. a compatibility build: target behaviour on the deployed migration chain (no data loss), or')
    print(f'  2. downgrade to {t_head} first, then redeploy the target.')
    if destructive:
        print(f'     DESTRUCTIVE: downgrade of {", ".join(destructive)} drops data (columns/tables/raw SQL).')
    return 1


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
