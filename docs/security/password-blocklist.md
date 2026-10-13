# Common/breached-password blocklist

**File:** `content/security/common-passwords.txt`. It is generated; do not
edit it by hand.

- **Builder:** `scripts/build_password_blocklist.py`.
- **Loaded by:** `_load_password_blocklist()` in `app.py`, at import.
- **Used by:** `_password_policy_problem()`, the `common` rule, for every new
  password (registration and change).

## Source and licence

| | |
|---|---|
| Collection | [SecLists](https://github.com/danielmiessler/SecLists), commit `913b327317496d062bcc7cace524aaad8a693be2` (2026-09-08) |
| Licence | **MIT** (SecLists' `LICENSE`, as reported by the GitHub licence API on 2026-10-12) |
| List 1 | `Passwords/Common-Credentials/100k-most-used-passwords-NCSC.txt`, SHA-256 `c2e5696882c603b76bb67a47ee970897e5a76fc4c3f5547abe3d0ca340c576e0`. The UK National Cyber Security Centre's 100,000 most used passwords, derived from Have I Been Pwned |
| List 2 | `Passwords/Common-Credentials/Pwdb_top-1000000.txt`, SHA-256 `e9a88f67aafe65496682dc374559ee714e978bee50314767494c3e37a18c9fc8`. The 1,000,000 most frequent passwords in the Pwdb compilation of public breach data |

**Licence of the underlying data:** SecLists redistributes both lists under
its MIT licence. Their upstream compilers (NCSC; Pwdb) published them for
defensive use.

- If the owner wants that confirmed by counsel, it's an **[OWNER/LEGAL]**
  item, not an engineering one.
- The shipped file contains only passwords, never usernames or other
  personal data.

## What is kept, and why only that

Each entry is decoded and normalised the same way the app normalises a
candidate password:

1. Hashcat `$hex[...]` encoding is decoded.
2. Non-UTF-8 entries are dropped, since no keyboard can type them.
3. The result is put in NFKC and case-folded.

Then only entries of **15–128 characters** are kept.

- 15 is the minimum for a new password, so a shorter entry can never be
  chosen, and checking it would be dead weight.
- Result: **5,524 entries, about 96 KB**. It is held in memory as one
  `frozenset` in the single web worker.
- **If the minimum is ever lowered, rebuild with `--min <new minimum>`.**
  The full ≥8-character set is about 600,000 entries (tens of MB in the
  worker), which is a different decision.

## Update procedure

1. Choose a newer SecLists commit, and read its changelog for the two files.
2. Download both files at that commit **into an empty scratch directory**
   (they are untrusted data). For example:

   ```
   gh api -H "Accept: application/vnd.github.raw" \
     "repos/danielmiessler/SecLists/contents/Passwords/Common-Credentials/100k-most-used-passwords-NCSC.txt?ref=<commit>" > ncsc.txt
   gh api -H "Accept: application/vnd.github.raw" \
     "repos/danielmiessler/SecLists/contents/Passwords/Common-Credentials/Pwdb_top-1000000.txt?ref=<commit>" > pwdb.txt
   sha256sum ncsc.txt pwdb.txt
   ```

3. Update `SOURCE_COMMIT` and `PINNED` in the builder **deliberately**, and
   this document's table. The builder refuses any file whose digest isn't
   pinned, so a changed upstream file can't slip in.
4. Run `python -I scripts/build_password_blocklist.py ncsc.txt pwdb.txt` and
   review the diff of `content/security/common-passwords.txt`.
5. Run the tests:
   - `tests/test_password_policy.py`: the header names the pinned sources;
     every entry is in comparison form and within the length bounds; the
     loaded count matches the file.
   - The full suite.
6. Release as usual. There's no migration: the list is code, and existing
   passwords are never re-judged against it.

Suggested cadence: yearly, or when SecLists publishes a materially larger
breach list.

## Failure behaviour

- **Missing file, no header separator, or no entries:** the app **refuses to
  start** (`RuntimeError` at import), because the alternative is silently
  accepting every common password.
- An entry may begin with `#`. The loader takes everything after the first
  blank line, so nothing in the body is treated as a comment.
