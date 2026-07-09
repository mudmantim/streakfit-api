"""StreakFit verification suite -- one module per subsystem.

Run the whole suite with `python scripts/verify_all.py <base-url>`, or
any single module standalone, e.g. `python scripts/verification/chat.py`.
See scripts/verification/README.md for the full module list and the
module contract new subsystems should follow.
"""

# Bump by hand whenever the check set changes meaningfully (a module is
# added/removed, or existing checks are materially rewritten) -- same
# discipline as the static/sw.js cache-version bump rule. This is what
# lets "128/128 passed" mean something specific six months from now
# instead of an ambiguous number. Update VERIFICATION_SUITE_UPDATED_AT
# (ISO date, UTC) in the same commit.
VERIFICATION_SUITE_VERSION = 2
VERIFICATION_SUITE_UPDATED_AT = "2026-07-08"
