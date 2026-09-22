# Incident — production backup passphrase disclosed, 2026-09-21

**Status:** remediated and verified. **Severity:** contained, but real.

## What happened

The owner encrypted the production database backup with the phrase
`StreakFit Neon Backup — September 21, 2026`, believing it to be a password
supplied by an AI assistant. That phrase was subsequently disclosed in a
third-party chat transcript and must be treated as public.

## Blast radius, established rather than assumed

| Artefact | Encrypted with the disclosed phrase? |
|---|---|
| `streakfit-neondb-production-20260921T185616Z.dump.gpg` | **Yes** — confirmed by decrypting it |
| Evidence key (`STREAKFIT_EVIDENCE_KEY`) | **No — it had never been created.** No `.gpg`, no `.id` file existed |

`gpg-agent` was restarted before the test so a cached passphrase could not
have produced a false positive.

The exposed file contained a full production dump: every user's data,
children's age bands, guardian links, consent records, and verbatim coach
conversations.

**What limited it:** the phrase was public, the *file* was not. It was local,
mode 600, never uploaded, never in the repository, never in a syncing folder.
Compromise needed both. What was lost was the margin — any future leak of that
file, by any route, would have been plaintext to anyone who read the
transcript.

## What was done

1. `streakfit-rewrap-backup.sh` re-encrypted the existing backup under a new,
   private passphrase chosen by the owner from a password manager and never
   shared with any assistant. The decrypt streams into the encrypt through a
   pipe, so no plaintext ever touched disk, and `PIPESTATUS` checks both
   stages independently.
2. The new file was verified before anything was destroyed: it opens with the
   new passphrase, passes gpg's MDC integrity check, and `pg_restore` parsed
   all 25 tables.
3. Only then: `shred -u` on the compromised file and on the staged copy of the
   old passphrase.

**Verified afterwards, independently:** the new file is present at mode 600,
`AES-256`; the old file and the staged passphrase are absent; no stray copies
exist anywhere under `$HOME`, `/tmp` or `/dev/shm`; and — the test that
mattered — **the disclosed phrase does NOT open the new file**, checked with a
restarted agent. The lock genuinely changed rather than the script merely
exiting zero.

## What changed in the tooling

All three gpg scripts now pass `--no-symkey-cache`.

This is **precautionary and labelled as such**. GnuPG's agent has a
symmetric-passphrase cache and the flag exists to disable it; the agent is per
*user*, so "open a new terminal" would not clear it, and a served cache during
`verify` would let that check pass without the operator reproducing the
passphrase — defeating its only purpose. But an attempt to reproduce reuse, on
both the encrypt and decrypt side, **failed**: with loopback pinentry nothing
was cached either way. The interactive pinentry path, which is what a human
actually uses, could not be driven from a script and remains untested. The
flag costs one extra passphrase entry and removes the question; it is not
there because a bug was observed.

For certainty rather than precaution: `gpgconf --kill gpg-agent` first.

## What this says about the process

The backup discipline held. No secret was ever in `argv`, in shell history, or
in a log; the file was mode 600 and outside the repository; and a verified
replacement existed before anything was destroyed. The failure was none of
those — it was a human being handed a passphrase by a chatbot and reasonably
assuming it was private.

**The rule that follows: a passphrase an assistant produced, saw, or could
have seen is not a passphrase.** Generate it in a password manager, store it
before using it, and never let it enter a chat — including this one. Any
assistant that offers to generate one is offering to become the single point
of failure for the thing it is protecting.
