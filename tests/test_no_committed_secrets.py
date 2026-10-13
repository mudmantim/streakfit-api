"""No real credential may be committed to this (public) repository.

The patterns and the placeholder rules live in scripts/secret_scan.py, shared
with the full-history scan (`python scripts/secret_scan.py --history`, run
locally: CI checks out a single commit). This test runs the tracked-files scan
in CI and pins the scanner's behaviour. Findings never include the value.

Context, 2026-10-13: staging's real Neon and Render identifiers were nearly
pushed in test fixtures, and the repository is public.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    'secret_scan', Path(__file__).resolve().parent.parent / 'scripts' / 'secret_scan.py')
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

# Real-looking values are assembled at runtime so this file never contains one
# literally (the scan reads this file too).
PW = 'Zq8v' + 'Kp2Lm9Rt'
HEX = 'a1b2c3d4' * 4 + '9f8e7d6c' * 4


def kinds(line):
    return [kind for _, kind in S.findings(line)]


def test_no_tracked_file_contains_a_real_credential():
    problems = S.scan_tracked()
    assert not problems, 'possible committed credentials (values withheld):\n  ' + '\n  '.join(problems)


def test_catches_each_credential_kind():
    cases = {
        'neon password': 'DATABASE_URL=postgresql://neondb_owner:npg_' + 'AbCdEf123456GhIj' + '@ep-x.neon.tech/db',
        'neon API key': 'NEON=napi_' + 'k' + HEX,
        'database/redis URL with a password': 'redis://default:' + PW + '@red-abc:6379',
        'conninfo password': "host=ep-x.neon.tech dbname=neondb user=app password=" + PW,
        'secret variable value': 'SECRET_KEY=' + HEX,
        'Render API key': 'rnd_' + 'a1B2c3D4e5F6g7H8i9J0k1L2',
        'Anthropic API key': 'sk-ant-api03-' + 'A1b2C3d4E5f6G7h8I9j0K1',
        'Resend API key': 're_' + 'Ab12Cd34_' + 'Ef56Gh78Ij90Kl12',
        'Stripe live key': 'sk_' + 'live_' + 'A1b2C3d4E5f6G7h8I9j0K1l2',
        'private key': '-----BEGIN OPENSSH PRIVATE ' + 'KEY-----',
        'AWS access key': 'AKIA' + 'ABCDEFGHIJKLMNOP',
        'GitHub token': 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8',
    }
    for kind, line in cases.items():
        assert kind in kinds(line), (kind, kinds(line))


def test_a_real_password_containing_a_placeholder_word_inside_a_token_is_still_caught():
    """Whole words only: 'Testarossa9' is not the word 'test'."""
    assert kinds('postgresql://app:' + 'Testarossa9Qz' + '@db.internal/x') == ['database/redis URL with a password']
    assert kinds('postgresql://app:' + 'Sup3rS3cretPassw0rd' + '@db.internal/x') != []


def test_obvious_placeholders_are_ignored():
    for line in ('postgresql://user:<password>@host/db', 'postgresql://u:p@localhost/db',
                 'postgresql://nobody:not-a-password@ep-odd-star-ajye3lag.c-2.us-east-2.aws.neon.tech/neondb',
                 'postgresql://postgres:ci-postgres-not-a-secret@127.0.0.1:5432',
                 'postgresql://postgres:$PW@127.0.0.1:55433/postgres',
                 'postgresql://postgres:{password}@localhost/db',
                 "f'postgresql://u:{PASSWORD}@host'",
                 'redis://default:redis-test-pw123@host:6379',
                 "SECRET_KEY='test-secret-key-not-for-production'",
                 'SECRET_KEY = os.environ.get("SECRET_KEY")',
                 'JWT_SECRET_KEY=' + 'x' * 32,
                 'host=localhost password=%s',
                 'JWT_SECRET_KEY=localdev-jwt-not-real',
                 'SECRET_KEY="build-check-not-for-production"',
                 'redis://user:SUPERSECRET@host:6379/0',
                 'ADMIN_SECRET=<generate: python -c "import secrets; print(secrets.token_hex(32))">'):
        assert kinds(line) == [], line


def test_findings_never_include_the_value():
    problems = [f'x:{n}: {k}' for n, k in S.findings('postgresql://app:' + PW + '@db.internal/x')]
    assert problems and PW not in ''.join(problems)
