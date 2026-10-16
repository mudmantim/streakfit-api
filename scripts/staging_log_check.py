#!/usr/bin/env python3
"""Judge one saved Render log of the RC-B staging rehearsal, mechanically.

    python3 scripts/staging_log_check.py deploy <log> --endpoint ep-... --service srv-... \\
        --commit <40-hex sha> --branch rc/safety-b --head <alembic head>
    python3 scripts/staging_log_check.py r2 <log> --service srv-... --commit <sha> --branch rc/safety-b
    python3 scripts/staging_log_check.py r1 <log> --service srv-... --commit <sha> --branch rc/safety-b

`deploy` judges a normal deploy (also the pooled-string deploy); `r2` the
replay with a dummy production-shaped URL; `r1` the replay with DATABASE_URL
removed. Every expectation is an argument, so the same tool cannot silently
pass a log of the wrong build, branch or service.

Successor of e2e-campaign/tools/staging_pw1/s07_log_check.py: every finding of
that tool is kept with the same ID and meaning; the additions are marked (v2).

v3 (2026-10-16): the runtime probe runs BEFORE gunicorn, and the Start Command
sets STREAKFIT_ENFORCE_DB_HEAD=1 (and, if ever, STREAKFIT_RETENTION_SWEEPER=1)
inline on gunicorn only, so the probe can never see them. The first real
rehearsal deploy proved it (RUN-HEAD-FLAG failed on a correctly configured
service). RUN-HEAD-FLAG and RUN-NO-SWEEPER now judge what the serving process
DID: the enforced head check ran and named the expected head, and no
retention sweeper event exists. The probe's view is only cross-checked.

OUTPUT: predefined findings only, `PASS|FAIL <ID> <description> [lines ...]`.
No log text is ever printed, not even identifiers; line numbers are the evidence.

Fails closed:
  * exit 0 only if every finding passes; 1 if any fails; 2 if the inputs
    cannot be judged at all (bad arguments, unreadable file);
  * evidence is taken only from inside the right probe block, and a probe
    block must be COMPLETE: every key the probe prints, exactly once, and no
    line that is not a probe line (v2);
  * the sequence must be in order: pre-deploy probe < migration < runtime
    probe < identity verified < head check;
  * exactly one of each event; anything duplicated is ambiguous and fails;
  * identifiers must be well-formed, and the expected ones may not be
    production's;
  * a truncated or empty log fails on whatever it lacks.
"""
import argparse
import re
import sys

CHECKER_VERSION = 'v3 2026-10-16'
PROD_ENDPOINT = 'ep-odd-star-ajye3lag'
# (2026-10-15) copies of production data: the incident recovery branch br-rough-smoke-ajnojcbg.
# Never a valid staging endpoint, and named nowhere in any rehearsal log, refusals included.
PROD_DERIVED = ('ep-dawn-fire-aj4zfzmz', 'br-rough-smoke-ajnojcbg')
PROD_SERVICE = 'srv-d8f1hh6rnols73ak3a10'
SERVICE_ID = re.compile(r'srv-[a-z0-9]{20}')
ENDPOINT = re.compile(r'ep-[a-z]+-[a-z]+-[a-z0-9]{8}')
FULL_SHA = re.compile(r'[0-9a-f]{40}')
ALEMBIC_REV = re.compile(r'[0-9a-f]{12}')
BRANCH = re.compile(r'[A-Za-z0-9._/-]{1,100}')
CREDENTIAL = re.compile(
    r'://[^/\s:@]+:[^@\s]+@'
    r'|(?i:\bpassword\s*=\s*\S)'
    r'|\bnpg_[A-Za-z0-9]{8,}|\bnapi_[A-Za-z0-9]{16,}|\brnd_[A-Za-z0-9]{16,}|\bsk-ant-[A-Za-z0-9_-]{8,}'
    r'|\bre_[A-Za-z0-9]{6,}_[A-Za-z0-9]{8,}'
    r'|\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.'
    r'|\b(?:SECRET_KEY|JWT_SECRET_KEY|ADMIN_SECRET|STREAKFIT_EVIDENCE_KEY|[A-Z_]*API_KEY)\s*[=:]\s*\S{6,}'
    # (v2) more providers' token shapes; nothing above was removed
    r'|\bghp_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\bAKIA[0-9A-Z]{16}\b'
    r'|-----BEGIN [A-Z ]*PRIVATE KEY-----'
    r'|(?i:\bbearer\s+[A-Za-z0-9._~+/=-]{20,})')

# What scripts/render_env_probe.py prints, in order (tests assert this equals
# the probe's own SHOWN + PRESENCE_ONLY, so the two cannot drift apart).
SHOWN = ('RENDER', 'RENDER_SERVICE_ID', 'RENDER_SERVICE_NAME', 'RENDER_SERVICE_TYPE', 'RENDER_INSTANCE_ID',
         'RENDER_GIT_COMMIT', 'RENDER_GIT_BRANCH', 'IS_PULL_REQUEST', 'STREAKFIT_DEPLOYMENT',
         'STREAKFIT_ENFORCE_DB_HEAD', 'STREAKFIT_RETENTION_SWEEPER')
PRESENCE_ONLY = ('DATABASE_URL', 'MIGRATION_DATABASE_URL', 'STREAKFIT_PRODUCTION_DB_ENDPOINT',
                 'STREAKFIT_ALLOW_PRODUCTION_DB_FROM_HERE')
EP_KEY = 'DATABASE_URL endpoint'
PROBE_KEYS = SHOWN + PRESENCE_ONLY + (EP_KEY,)
# The overrides that could point a process at another database: absent throughout the rehearsal.
OVERRIDES = ('MIGRATION_DATABASE_URL', 'STREAKFIT_PRODUCTION_DB_ENDPOINT', 'STREAKFIT_ALLOW_PRODUCTION_DB_FROM_HERE')

HEADER = re.compile(r'render_env_probe phase=([\w-]+)(?: (error=\S+))?')
_KEYS_RX = '|'.join(re.escape(k) for k in sorted(PROBE_KEYS, key=len, reverse=True))
KEYLINE = re.compile(rf'(?:^|\s)({_KEYS_RX})(?:=(\S+)|: (MISSING|set))\s*$')
VERDICT = re.compile(r'production-in-this-phase: (PASS|FAIL)\b')
MIGRATING = re.compile(r'Migrating database: deployment=(\S+) endpoint=(\S+) hosted=\S+ service=(\S+) '
                       r'\(server endpoint ([^)]+)\)')
IDENTITY = re.compile(r'Database identity verified \(deployment=(\S+) endpoint=(\S+) hosted=\S+ '
                      r'service=([^,]+), server endpoint ([^)]+)\)')
HEAD_OK = re.compile(r'Database migration check passed(?: \(at head ([0-9a-f]+)\))?')
# (v3) app._assert_db_at_head() refusing to boot: wrong revision, or state unverifiable
HEAD_REFUSAL = re.compile(r'Refusing to start: (?:database is at Alembic revision|could not verify database '
                          r'migration state)')
# (v3) app._start_retention_sweeper() logs one of these whenever STREAKFIT_RETENTION_SWEEPER=1
SWEEPER_EVENT = re.compile(r'event=retention_sweeper_\w+')
REFUSED = re.compile(r'Refusing to start: (R\d)\b')
CHECKOUT = re.compile(r'(?i)checking out commit ([0-9a-f]{7,40})')


class Report:
    def __init__(self):
        self.rows = []

    def check(self, fid, text, ok, *lines):
        self.rows.append((fid, text, bool(ok), sorted({ln + 1 for ln in lines if ln is not None})))
        return bool(ok)

    @property
    def ok(self):
        return bool(self.rows) and all(r[2] for r in self.rows)

    def failed_ids(self):
        return [r[0] for r in self.rows if not r[2]]


def _all(lines, rx):
    return [(i, m) for i, ln in enumerate(lines) for m in [rx.search(ln)] if m]


def probe_blocks(lines):
    """[{'phase', 'error', 'at', 'end', 'kv': {key: [(value, i)]}, 'verdict': [(v, i)], 'stray': [i]}].
    A block runs from its header to its verdict line (or to the next header /
    end of log if the verdict never comes). A non-blank line inside a block
    that is not a probe line is `stray` (v2)."""
    blocks, cur = [], None
    for i, line in enumerate(lines):
        h = HEADER.search(line)
        if h:
            if cur:
                blocks.append(cur)
            cur = {'phase': h.group(1), 'error': h.group(2), 'at': i, 'end': i, 'kv': {}, 'verdict': [],
                   'stray': []}
            continue
        if cur is None:
            continue
        v = VERDICT.search(line)
        k = KEYLINE.search(line)
        if v:
            cur['end'] = i
            cur['verdict'].append((v.group(1), i))
            blocks.append(cur)
            cur = None
        elif k:
            cur['end'] = i
            cur['kv'].setdefault(k.group(1), []).append((k.group(2) or k.group(3), i))
        elif line.strip():
            # an unterminated block ends at the first line that is not part of it,
            # so later log lines are never mistaken for probe evidence
            cur['stray'].append(i)
            blocks.append(cur)
            cur = None
    if cur:
        blocks.append(cur)
    return blocks


def _one(block, key):
    """The single value of `key` in the block, or None if absent or ambiguous."""
    vals = block['kv'].get(key, [])
    return vals[0] if len(vals) == 1 else None


def _val(block, key):
    one = _one(block, key)
    return one[0] if one else None


def _at(block, key):
    one = _one(block, key)
    return one[1] if one else block['at']


def check_args(rep, args, need_endpoint):
    ok = True
    if need_endpoint:
        ok &= rep.check('ARG-EP', 'staging endpoint argument is well-formed and is not production',
                        args.endpoint and ENDPOINT.fullmatch(args.endpoint) and args.endpoint != PROD_ENDPOINT
                        and args.endpoint not in PROD_DERIVED)
        ok &= rep.check('ARG-HEAD', '(v2) expected Alembic head argument is a 12-hex revision',
                        args.head and ALEMBIC_REV.fullmatch(args.head))
    ok &= rep.check('ARG-SID', '(v2) expected service argument is well-formed and is not production',
                    args.service and SERVICE_ID.fullmatch(args.service) and args.service != PROD_SERVICE)
    ok &= rep.check('ARG-COMMIT', '(v2) expected commit argument is a full 40-hex SHA',
                    args.commit and FULL_SHA.fullmatch(args.commit))
    ok &= rep.check('ARG-BRANCH', '(v2) expected branch argument is well-formed and is not main',
                    args.branch and BRANCH.fullmatch(args.branch) and args.branch not in ('main', 'master'))
    return ok


def check_block_shape(rep, tag, b):
    dup = [k for k, v in b['kv'].items() if len(v) > 1] + (['verdict'] if len(b['verdict']) > 1 else [])
    rep.check(f'{tag}-UNAMBIGUOUS', f'{tag.lower()} probe block has each key at most once', not dup, b['at'])
    missing = [k for k in PROBE_KEYS if k not in b['kv']] + ([] if b['verdict'] else ['verdict'])
    rep.check(f'{tag}-COMPLETE', f'(v2) {tag.lower()} probe block is complete: every probe key, a verdict, '
              'no stray line, no probe error', not missing and not b['stray'] and not b['error'],
              b['at'], *b['stray'][:5])


def check_build(rep, tag, b, args):
    """(v2) The block belongs to the expected build, branch and service, declared staging."""
    rep.check(f'{tag}-COMMIT', f'(v2) {tag.lower()}: RENDER_GIT_COMMIT is exactly the expected release commit',
              _val(b, 'RENDER_GIT_COMMIT') == args.commit, _at(b, 'RENDER_GIT_COMMIT'))
    rep.check(f'{tag}-BRANCH', f'(v2) {tag.lower()}: RENDER_GIT_BRANCH is the expected branch',
              _val(b, 'RENDER_GIT_BRANCH') == args.branch, _at(b, 'RENDER_GIT_BRANCH'))
    rep.check(f'{tag}-SERVICE', f'(v2) {tag.lower()}: RENDER_SERVICE_ID is the expected staging service',
              _val(b, 'RENDER_SERVICE_ID') == args.service, _at(b, 'RENDER_SERVICE_ID'))
    rep.check(f'{tag}-DECL', f'(v2) {tag.lower()}: STREAKFIT_DEPLOYMENT=staging is declared',
              _val(b, 'STREAKFIT_DEPLOYMENT') == 'staging', _at(b, 'STREAKFIT_DEPLOYMENT'))
    bad = [k for k in OVERRIDES if _val(b, k) != 'MISSING']
    rep.check(f'{tag}-NO-OVERRIDE', f'(v2) {tag.lower()}: no database override variable is set',
              not bad, *[_at(b, k) for k in bad])


def check_deploy(lines, args, rep):
    blocks = probe_blocks(lines)
    pre = [b for b in blocks if b['phase'] == 'pre-deploy']
    run = [b for b in blocks if b['phase'] == 'runtime']
    other = [b for b in blocks if b['phase'] not in ('pre-deploy', 'runtime')]
    rep.check('ONE-DEPLOY', 'exactly one pre-deploy and one runtime probe block, no other phase',
              len(pre) == 1 and len(run) == 1 and not other, *[b['at'] for b in blocks])
    if len(pre) != 1 or len(run) != 1:
        return
    pre, run = pre[0], run[0]
    check_block_shape(rep, 'PRE', pre)
    check_block_shape(rep, 'RUN', run)
    check_build(rep, 'PRE', pre, args)
    check_build(rep, 'RUN', run, args)

    sid = _one(pre, 'RENDER_SERVICE_ID')
    rep.check('PRE-SID', 'Pre-Deploy sees a well-formed RENDER_SERVICE_ID (THE open question)',
              sid and SERVICE_ID.fullmatch(sid[0]), sid[1] if sid else pre['at'])
    verdict = pre['verdict'][0] if len(pre['verdict']) == 1 else None
    rep.check('PRE-VERDICT', 'Pre-Deploy verdict: production would be accepted',
              verdict and verdict[0] == 'PASS', verdict[1] if verdict else pre['at'])
    ep = _one(pre, EP_KEY)
    rep.check('PRE-EP', 'Pre-Deploy DATABASE_URL names the staging endpoint',
              ep and ep[0] == args.endpoint, ep[1] if ep else pre['at'])
    rep.check('RUN-EP', '(v2) runtime DATABASE_URL names the staging endpoint',
              _val(run, EP_KEY) == args.endpoint, _at(run, EP_KEY))
    # (v3) The probe cannot see a flag set inline on gunicorn; its value proves nothing. Proof is the
    # serving process's own behaviour: no sweeper event anywhere in the log.
    sweeps = [i for i, ln in enumerate(lines) if SWEEPER_EVENT.search(ln)]
    rep.check('RUN-NO-SWEEPER', '(v3) the retention sweeper is NOT enabled: the probe does not show it set and '
              'no retention sweeper event was logged',
              _val(run, 'STREAKFIT_RETENTION_SWEEPER') == 'MISSING' and not sweeps,
              _at(run, 'STREAKFIT_RETENTION_SWEEPER'), *sweeps[:10])

    migs = _all(lines, MIGRATING)
    mig = migs[0] if len(migs) == 1 else None
    rep.check('MIG-ONE', 'exactly one "Migrating database" line', mig, *[i for i, _ in migs])
    if mig:
        i, m = mig
        rep.check('MIG-ORDER', 'migration is after the Pre-Deploy probe and before the runtime probe',
                  pre['end'] < i < run['at'], i)
        rep.check('MIG-STAGING', 'migration ran as declared staging on the staging endpoint',
                  m.group(1) == 'staging' and m.group(2) == args.endpoint, i)
        rep.check('MIG-SERVER', 'migration: the SERVER reported the staging endpoint (R7)',
                  m.group(4) == args.endpoint, i)
        rep.check('MIG-SID', 'migration reports the same service ID as the Pre-Deploy probe',
                  sid and m.group(3) == sid[0], i)

    rverdict = run['verdict'][0] if len(run['verdict']) == 1 else None
    rep.check('RUN-VERDICT', 'runtime verdict PASS', rverdict and rverdict[0] == 'PASS',
              rverdict[1] if rverdict else run['at'])
    rsid = _one(run, 'RENDER_SERVICE_ID')
    rep.check('RUN-SID', 'runtime service ID equals the Pre-Deploy one', rsid and sid and rsid[0] == sid[0],
              rsid[1] if rsid else run['at'])

    idents = _all(lines, IDENTITY)
    ident = idents[0] if len(idents) == 1 else None
    rep.check('ID-ONE', 'exactly one "Database identity verified" line', ident, *[i for i, _ in idents])
    if ident:
        i, m = ident
        rep.check('ID-ORDER', 'identity check is after the runtime probe', i > run['end'], i)
        rep.check('ID-STAGING', 'boot: declared staging, staging endpoint, server reported the staging endpoint',
                  m.group(1) == 'staging' and m.group(2) == args.endpoint and m.group(4) == args.endpoint, i)
        rep.check('ID-SID', 'boot reports the same service ID', sid and m.group(3) == sid[0], i)
    heads = _all(lines, HEAD_OK)
    rep.check('HEAD-ONE', 'exactly one "Database migration check passed", after the identity check',
              len(heads) == 1 and ident and heads[0][0] > ident[0], *[i for i, _ in heads])
    rep.check('HEAD-REV', '(v2) the head check names the expected Alembic head',
              len(heads) == 1 and heads[0][1].group(1) == args.head, *[i for i, _ in heads])
    # (v3) Positive evidence that the serving process ran its ENFORCED head check: the only code that logs
    # "Database migration check passed" is app._assert_db_at_head(), called only when
    # STREAKFIT_ENFORCE_DB_HEAD == '1'. So require that line, once, naming the expected head, after the
    # runtime probe and the identity check, and no head-check refusal. The probe's value of the flag is
    # only cross-checked (MISSING: set inline on gunicorn, as the Start Command does; 1: set in the
    # service environment); it is never taken as proof on its own.
    head_refusals = [i for i, _ in _all(lines, HEAD_REFUSAL)]
    flag = _val(run, 'STREAKFIT_ENFORCE_DB_HEAD')
    rep.check('RUN-HEAD-FLAG', '(v3) the serving process ran its enforced head check: exactly one head check '
              'naming the expected head, after the runtime probe and the identity check, no head-check refusal, '
              'probe flag MISSING (inline) or 1',
              len(heads) == 1 and heads[0][1].group(1) == args.head and heads[0][0] > run['end']
              and ident and heads[0][0] > ident[0] and not head_refusals and flag in ('MISSING', '1'),
              _at(run, 'STREAKFIT_ENFORCE_DB_HEAD'), *[i for i, _ in heads], *head_refusals[:10])
    prod = [i for i, ln in enumerate(lines) if PROD_ENDPOINT in ln or PROD_SERVICE in ln
            or any(d in ln for d in PROD_DERIVED)]
    rep.check('NO-PROD', 'production endpoint (v2: or service, or a copy of production data) appears nowhere in '
              'this log', not prod, *prod[:10])
    refused = [i for i, _ in _all(lines, REFUSED)]
    rep.check('NO-REFUSAL', 'no "Refusing to start"', not refused, *refused[:10])


def check_refusal(lines, rule, args, rep):
    blocks = probe_blocks(lines)
    pre = [b for b in blocks if b['phase'] == 'pre-deploy']
    rep.check('ONE-PRE', 'exactly one pre-deploy probe block', len(pre) == 1, *[b['at'] for b in pre])
    refusals = _all(lines, REFUSED)
    rep.check('REFUSED', f'exactly one "Refusing to start", with rule {rule}',
              len(refusals) == 1 and refusals[0][1].group(1) == rule, *[i for i, _ in refusals])
    if len(pre) == 1:
        check_block_shape(rep, 'PRE', pre[0])
        check_build(rep, 'PRE', pre[0], args)
        ep = _one(pre[0], EP_KEY)
        want = PROD_ENDPOINT if rule == 'R2' else 'none'
        rep.check('SETUP', f'the step\'s setup was really in place (Pre-Deploy endpoint={want})',
                  ep and ep[0] == want, ep[1] if ep else pre[0]['at'])
        if rule == 'R1':
            rep.check('SETUP-NO-URL', '(v2) DATABASE_URL really was absent',
                      _val(pre[0], 'DATABASE_URL') == 'MISSING', _at(pre[0], 'DATABASE_URL'))
        if refusals:
            rep.check('ORDER', 'the refusal comes after the Pre-Deploy probe', refusals[0][0] > pre[0]['end'],
                      refusals[0][0])
    if rule == 'R2' and len(refusals) == 1:
        line = lines[refusals[0][0]]
        rep.check('NAMES-PROD', 'the refusal names the production endpoint', PROD_ENDPOINT in line, refusals[0][0])
        # (v2) Only a process that HAS its service ID is refused as "service X is not a production
        # service"; without one, a declared-staging process is refused by R3 instead. So this
        # line is a second, independent proof that Pre-Deploy sees RENDER_SERVICE_ID.
        rep.check('NAMES-SID', '(v2) the R2 refusal names the expected staging service (Pre-Deploy has its ID)',
                  f'Render service {args.service} is not a production service' in line, refusals[0][0])
    ran = [i for i, ln in enumerate(lines) if 'Migrating database:' in ln or 'Running upgrade' in ln]
    rep.check('NO-MIGRATION', 'no migration ran', not ran, *ran[:10])
    booted = [i for i, ln in enumerate(lines)
              if HEAD_OK.search(ln) or IDENTITY.search(ln) or 'render_env_probe phase=runtime' in ln]
    rep.check('NOT-BOOTED', 'the new build never booted', not booted, *booted[:10])


def evaluate(mode, lines, args):
    rep = Report()
    if not check_args(rep, args, need_endpoint=(mode == 'deploy')):
        return rep
    if not rep.check('NONEMPTY', 'the log file is not empty', any(ln.strip() for ln in lines)):
        return rep
    if mode == 'deploy':
        check_deploy(lines, args, rep)
    else:
        check_refusal(lines, mode.upper(), args, rep)
    # (v2) Render's own checkout line, when the saved range includes it, must name the same commit.
    co = _all(lines, CHECKOUT)
    rep.check('CHECKOUT', '(v2) every "Checking out commit" line names the expected commit',
              all(args.commit and args.commit.startswith(m.group(1)) for _, m in co), *[i for i, _ in co])
    derived = [i for i, ln in enumerate(lines) if any(d in ln for d in PROD_DERIVED)]
    rep.check('NO-PROD-COPY', '(2026-10-15) no copy of production data (recovery branch) is named anywhere',
              not derived, *derived[:10])
    leaked = [i for i, ln in enumerate(lines) if CREDENTIAL.search(ln)]
    rep.check('NO-CREDENTIAL', 'no credential-shaped text anywhere in the log', not leaked, *leaked[:10])
    return rep


def parse(argv):
    p = argparse.ArgumentParser(description='RC-B staging rehearsal log check (prints findings only)')
    p.add_argument('mode', choices=('deploy', 'r1', 'r2'))
    p.add_argument('log')
    p.add_argument('--endpoint', help='expected staging Neon endpoint (deploy)')
    p.add_argument('--head', help='expected Alembic head (deploy)')
    p.add_argument('--service', required=True, help='expected staging Render service ID')
    p.add_argument('--commit', required=True, help='expected release commit, full 40-hex SHA')
    p.add_argument('--branch', required=True, help='expected Render branch')
    return p.parse_args(argv)


def main(argv):
    try:
        args = parse(argv)
    except SystemExit:
        print('UNVERIFIABLE: bad arguments; nothing was judged')
        return 2
    try:
        with open(args.log, encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError as exc:
        print(f'UNVERIFIABLE: cannot read the log ({type(exc).__name__}); nothing was judged')
        return 2
    rep = evaluate(args.mode, lines, args)
    for fid, text, ok, at in rep.rows:
        print(f'{"PASS" if ok else "FAIL"}  {fid:17} {text}' + (f'  [lines {", ".join(map(str, at))}]' if at else ''))
    print('rehearsal', args.mode, 'PASSED' if rep.ok else 'FAILED', f'(checker {CHECKER_VERSION})')
    return 0 if rep.ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
