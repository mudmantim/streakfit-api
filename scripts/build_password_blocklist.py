#!/usr/bin/env python3
"""Build content/security/common-passwords.txt from pinned public lists.

    python -I scripts/build_password_blocklist.py <ncsc.txt> <pwdb.txt> [--min 15]

Sources (SecLists, MIT licence, commit SOURCE_COMMIT):
  Passwords/Common-Credentials/100k-most-used-passwords-NCSC.txt
      The UK National Cyber Security Centre's list of the 100,000 most used
      passwords, derived from Have I Been Pwned's breach corpus.
  Passwords/Common-Credentials/Pwdb_top-1000000.txt
      The 1,000,000 most frequent passwords in the Pwdb compilation of public
      breach data.

Download them yourself (they are not committed); this script refuses any file
whose SHA-256 is not the pinned one, so the output is reproducible and a
changed upstream file cannot slip in unnoticed. docs/security/password-blocklist.md
has the update procedure.

Only entries that a new password could actually be are kept: after the same
normalisation the app applies (NFKC, then casefold), at least `--min`
characters (the password minimum) and at most 128. Entries shorter than the
minimum can never be chosen, so carrying them would cost memory in the single
web worker for nothing. Hashcat's $hex[...] encoding is decoded; entries that
are not valid UTF-8 are dropped (no keyboard can type them).
"""
import argparse
import hashlib
import os
import sys
import unicodedata

SOURCE_REPO = 'https://github.com/danielmiessler/SecLists'
SOURCE_COMMIT = '913b327317496d062bcc7cace524aaad8a693be2'
PINNED = {
    'ncsc': ('Passwords/Common-Credentials/100k-most-used-passwords-NCSC.txt',
             'c2e5696882c603b76bb67a47ee970897e5a76fc4c3f5547abe3d0ca340c576e0'),
    'pwdb': ('Passwords/Common-Credentials/Pwdb_top-1000000.txt',
             'e9a88f67aafe65496682dc374559ee714e978bee50314767494c3e37a18c9fc8'),
}
MAX_LENGTH = 128
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'content', 'security', 'common-passwords.txt')


def normalise(text):
    """The app's comparison form (app._password_compare_form)."""
    return unicodedata.normalize('NFKC', text).casefold()


def decode_line(raw):
    """One list line (bytes) -> str, or None if it is not a typeable password."""
    raw = raw.rstrip(b'\r')
    if raw.startswith(b'$HEX[') or raw.startswith(b'$hex['):
        if not raw.endswith(b']'):
            return None
        try:
            raw = bytes.fromhex(raw[5:-1].decode('ascii'))
        except ValueError:
            return None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return None


def entries(data, minimum):
    for line in data.split(b'\n'):
        text = decode_line(line)
        if not text:
            continue
        n = normalise(text)
        if minimum <= len(n) <= MAX_LENGTH and '\n' not in n:
            yield n


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('ncsc')
    p.add_argument('pwdb')
    p.add_argument('--min', type=int, default=15)
    p.add_argument('--out', default=OUT)
    a = p.parse_args(argv)
    kept = set()
    for key, path in (('ncsc', a.ncsc), ('pwdb', a.pwdb)):
        data = open(path, 'rb').read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != PINNED[key][1]:
            print(f'{path}: SHA-256 {digest} is not the pinned {PINNED[key][1]}. '
                  'Update PINNED (and the docs) deliberately, or download the pinned commit.',
                  file=sys.stderr)
            return 1
        kept.update(entries(data, a.min))
    header = [
        '# StreakFit common/breached-password blocklist. GENERATED -- do not edit by hand.',
        '# Built by scripts/build_password_blocklist.py; procedure in docs/security/password-blocklist.md.',
        f'# Source: {SOURCE_REPO} @ {SOURCE_COMMIT} (MIT licence)',
    ] + [f'#   {PINNED[k][0]} sha256={PINNED[k][1]}' for k in ('ncsc', 'pwdb')] + [
        f'# Kept: entries of {a.min}-{MAX_LENGTH} characters after NFKC + casefold. Count: {len(kept)}',
    ]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8', newline='\n') as f:
        # Header, ONE blank line, then entries. The loader takes everything
        # after the first blank line, so an entry may begin with '#'.
        f.write('\n'.join(header + [''] + sorted(kept)) + '\n')
    print(f'wrote {len(kept)} entries to {a.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
