"""No real credential may be committed to this (public) repository.

Every tracked text file is scanned for the shapes of the credentials StreakFit
actually uses -- Neon passwords and connection strings, Render / Anthropic /
Resend API keys, private keys, cloud keys. A match fails CI with the file and
line, never the matched value.

Placeholders and synthetic test values are recognised by shape (angle
brackets, ellipses, words like `password`/`secret`/`example`/`dummy`/`test`),
not by an allow-list of real-looking strings. A new false positive should be
fixed by making the test value LOOK synthetic, not by widening this file.

Context, 2026-10-13: staging's real Neon and Render identifiers were nearly
pushed in test fixtures, and the repository is public. Identifiers are not
credentials and are not scanned for here; the passwords and keys are.
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.woff', '.woff2', '.ttf', '.pdf', '.gz'}

# A password/key segment is synthetic if it contains any of these.
_SYNTHETIC = re.compile(
    r'(?i)<|>|\.\.\.|\*\*\*|\{|\}|\$|password|passw0rd|secret|example|dummy|placeholder|sentinel|'
    r'not-a|fake|test|throwaway|change-?me|redacted|xxx|kit|rehearsal|probe|nobody|ci-')

PATTERNS = {
    # Neon role passwords are issued with this prefix.
    'neon password': re.compile(r'\bnpg_[A-Za-z0-9]{12,}'),
    # user:password@host for postgres / redis URLs; judged by the password part.
    'database/redis URL with a password': re.compile(
        r'\b(?:postgres(?:ql)?(?:\+\w+)?|rediss?)://[^\s:/@\'"]+:(?P<pw>[^\s@\'"]{6,})@[^\s/\'"]+'),
    'Render API key': re.compile(r'\brnd_[A-Za-z0-9]{20,}'),
    'Anthropic API key': re.compile(r'\bsk-ant-[A-Za-z0-9_-]{20,}'),
    'Resend API key': re.compile(r'\bre_[A-Za-z0-9]{8,}_[A-Za-z0-9]{12,}'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'AWS access key': re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    'GitHub token': re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}'),
}


def tracked_text_files():
    out = subprocess.run(['git', 'ls-files', '-z'], cwd=REPO, capture_output=True, check=True).stdout
    for name in out.decode().split('\0'):
        if not name or Path(name).suffix.lower() in SKIP_SUFFIXES:
            continue
        path = REPO / name
        try:
            yield name, path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue


def findings(text):
    for lineno, line in enumerate(text.splitlines(), 1):
        for label, pattern in PATTERNS.items():
            for m in pattern.finditer(line):
                secret = m.groupdict().get('pw') or m.group(0)
                if _SYNTHETIC.search(secret):
                    continue
                if label.startswith('database') and len(set(secret)) <= 3:   # 'aaaaaa', 'x' * n
                    continue
                yield lineno, label


def test_no_tracked_file_contains_a_real_credential():
    problems = [f'{name}:{lineno}: {label}'
                for name, text in tracked_text_files() for lineno, label in findings(text)]
    assert not problems, 'possible committed credentials (values withheld):\n  ' + '\n  '.join(problems)


# -- the scanner itself ----------------------------------------------------------

def _hits(line):
    return [label for _, label in findings(line)]


# Real-looking values are assembled at runtime so this file never contains
# one literally (the scan above reads this file too).
PW = 'Zq8v' + 'Kp2Lm9Rt'


def test_scanner_catches_real_looking_credentials():
    assert _hits('DATABASE_URL=postgresql://neondb_owner:npg_' + 'AbCdEf123456GhIj' + '@ep-x.neon.tech/neondb') != []
    assert 'database/redis URL with a password' in _hits('postgresql://app:' + PW + '@db.internal/x')
    assert 'database/redis URL with a password' in _hits('redis://default:' + PW + '@red-abc:6379')
    assert _hits('key = "sk-ant-api03-' + 'A1b2C3d4E5f6G7h8I9j0K1' + '"') == ['Anthropic API key']
    assert _hits('RENDER=rnd_' + 'a1B2c3D4e5F6g7H8i9J0k1L2') == ['Render API key']
    assert _hits('RESEND_API_KEY=re_' + 'Ab12Cd34_' + 'Ef56Gh78Ij90Kl12') == ['Resend API key']
    assert _hits('-----BEGIN OPENSSH PRIVATE KEY-----') == ['private key']


def test_scanner_ignores_obvious_placeholders():
    for line in ('postgresql://user:<password>@host/db', 'postgresql://u:p@localhost/db',
                 'postgresql://neondb_owner:Sup3rS3cretPassw0rd@ep-x.neon.tech/neondb',
                 'postgresql://nobody:not-a-password@ep-odd-star-ajye3lag.c-2.us-east-2.aws.neon.tech/neondb',
                 'postgresql://postgres:ci-postgres-not-a-secret@127.0.0.1:5432',
                 'postgresql://postgres:$PW@127.0.0.1:55433/postgres',
                 'postgresql://postgres:{password}@localhost/db',
                 "f'postgresql://u:{PASSWORD}@host'"):
        assert _hits(line) == [], line


def test_findings_never_include_the_value():
    problems = [f'x:{n}: {label}' for n, label in findings('postgresql://app:' + PW + '@db.internal/x')]
    assert problems and PW not in ''.join(problems)
