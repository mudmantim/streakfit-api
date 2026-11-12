#!/usr/bin/env python3
"""Credential-shape scanner shared by the CI test and the history scan.

    python scripts/secret_scan.py            # current tracked files (what CI runs)
    python scripts/secret_scan.py --history  # every commit reachable from any ref

Findings name the place (file:line, or commit:file for history) and the kind
of credential, NEVER the value. Exit 1 if anything is found.

What it looks for: the credentials StreakFit actually uses or could plausibly
hold -- Neon passwords and API keys, postgres/redis URLs and libpq conninfo with
a password, values assigned to the app's own secret variables, Render /
Anthropic / Resend / Stripe-live / GitHub / AWS keys, JSON Web Tokens, private keys.

What counts as synthetic (not reported): values whose SHAPE says placeholder.
That means angle brackets, braces, shell/f-string interpolation, ellipses or
asterisks, or a whole word such as test, example, dummy, placeholder,
sentinel, fake, changeme, redacted. Whole words only, so a real password that
merely contains "test" inside a longer token is still reported. Fix a false
positive by making the test value look synthetic, not by widening this list.

Identifiers (Neon endpoint IDs, Render service IDs) are not credentials and are
not reported.

CI checks out a single commit, so it can only run the tracked-files scan. The
--history scan is run locally (it needs full history); see
docs/operations/secret-scanning.md.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.woff', '.woff2', '.ttf', '.pdf', '.gz',
                 '.zip', '.mp4', '.mov'}

_SYNTHETIC_MARKS = re.compile(r'[<>{}$]|\.\.\.|\*\*\*|%\(|%s')
_SYNTHETIC_WORDS = re.compile(
    r'(?i)(?:^|[^a-z0-9])(?:test|tests|testing|example|examples|dummy|placeholder|sentinel|fake|'
    r'changeme|change-me|redacted|notasecret|not-a-secret|not-a-password|password|passw0rd|secret|'
    r'nobody|ci|kit|rehearsal|probe|throwaway|xxx+|your[-_a-z]*|supersecret|localdev|local-dev|'
    r'not-real|not_real|not-for-production|not_for_production)(?:$|[^a-z0-9])')

SECRET_VARS = ('SECRET_KEY', 'JWT_SECRET_KEY', 'ADMIN_SECRET', 'STREAKFIT_EVIDENCE_KEY', 'RESEND_API_KEY',
               'ANTHROPIC_API_KEY', 'NEON_API_KEY', 'RENDER_API_KEY')

PATTERNS = {
    'neon password': re.compile(r'\bnpg_[A-Za-z0-9]{12,}'),
    'neon API key': re.compile(r'\bnapi_[A-Za-z0-9]{30,}'),
    'database/redis URL with a password': re.compile(
        r'\b(?:postgres(?:ql)?(?:\+\w+)?|rediss?)://[^\s:/@\'"]+:(?P<value>[^\s@\'"]{6,})@[^\s/\'"]+'),
    'conninfo password': re.compile(
        r'(?i)\bhost\s*=\s*\S+.*?\bpassword\s*=\s*(?P<value>\'[^\']{6,}\'|"[^"]{6,}"|[^\s\'"]{6,})'),
    'secret variable value': re.compile(
        r'\b(?:' + '|'.join(SECRET_VARS) + r')\b["\']?\s*[:=]\s*["\']?(?P<value>[A-Za-z0-9_\-+/=.]{20,})'),
    'Render API key': re.compile(r'\brnd_[A-Za-z0-9]{20,}'),
    'Anthropic API key': re.compile(r'\bsk-ant-[A-Za-z0-9_-]{20,}'),
    'Resend API key': re.compile(r'\bre_[A-Za-z0-9]{8,}_[A-Za-z0-9]{12,}'),
    'Stripe live key': re.compile(r'\b(?:sk|rk)_live_[A-Za-z0-9]{20,}'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'AWS access key': re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    # header.payload.signature, both JSON parts base64url ('eyJ' = '{"'). A
    # header alone (an API doc example) has no payload and is not a token.
    # Gitleaks found two committed in photostate.json that this scanner missed.
    'JSON Web Token': re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
}


def is_synthetic(value: str) -> bool:
    if _SYNTHETIC_MARKS.search(value) or _SYNTHETIC_WORDS.search(value):
        return True
    return len(set(value)) <= 3          # 'aaaaaaaa', 'x' * 32


def findings(text: str):
    """Yield (line number, kind) for every credential-shaped value that does not
    look synthetic. Never yields the value."""
    for lineno, line in enumerate(text.splitlines(), 1):
        for kind, pattern in PATTERNS.items():
            for m in pattern.finditer(line):
                value = (m.groupdict().get('value') or m.group(0)).strip('\'"')
                if kind == 'private key' or not is_synthetic(value):
                    yield lineno, kind


def tracked_files():
    """Tracked files AND untracked files that are not ignored. A new file is
    scanned before it is committed, so a local run sees what CI will see.
    (The first version read tracked files only: it passed locally on a test
    file that was not yet committed, and failed in CI the moment it was.)"""
    out = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=REPO,
                         capture_output=True, check=True).stdout
    for name in out.decode().split('\0'):
        if not name or Path(name).suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            yield name, (REPO / name).read_text(encoding='utf-8')
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue


def scan_tracked() -> list:
    return [f'{name}:{lineno}: {kind}' for name, text in tracked_files() for lineno, kind in findings(text)]


def history_exceptions() -> list:
    """Full blob SHAs reviewed as synthetic (scripts/secret_scan_history_exceptions.txt)."""
    path = REPO / 'scripts' / 'secret_scan_history_exceptions.txt'
    if not path.exists():
        return []
    shas = []
    for line in path.read_text().splitlines():
        sha = line.split('#', 1)[0].strip()
        if sha:
            if not re.fullmatch(r'[0-9a-f]{40}', sha):
                raise SystemExit(f'{path.name}: not a full blob SHA: {sha!r}')
            shas.append(sha)
    return shas


def scan_history() -> list:
    """Every blob ever reachable from any ref (branches, tags, remotes), each
    scanned once. Reports the first commit/path where each finding occurs."""
    revs = subprocess.run(['git', 'rev-list', '--all', '--objects'], cwd=REPO, capture_output=True, text=True,
                          check=True).stdout.splitlines()
    problems, seen = [], set(history_exceptions())
    for line in revs:
        parts = line.split(' ', 1)
        if len(parts) != 2 or Path(parts[1]).suffix.lower() in SKIP_SUFFIXES:
            continue
        sha, path = parts
        if sha in seen:
            continue
        seen.add(sha)
        kind_of = subprocess.run(['git', 'cat-file', '-t', sha], cwd=REPO, capture_output=True, text=True).stdout
        if kind_of.strip() != 'blob':
            continue
        data = subprocess.run(['git', 'cat-file', 'blob', sha], cwd=REPO, capture_output=True).stdout
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            continue
        for lineno, kind in findings(text):
            problems.append(f'blob {sha[:12]} ({path}):{lineno}: {kind}')
    return problems


def main(argv: list) -> int:
    history = '--history' in argv
    problems = scan_history() if history else scan_tracked()
    scope = 'repository history (all refs)' if history else 'the working tree (tracked + untracked, not ignored)'
    if problems:
        print(f'possible committed credentials in {scope} (values withheld):')
        for p in problems:
            print('  ' + p)
        return 1
    print(f'no credential-shaped values found in {scope}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
