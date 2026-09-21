#!/usr/bin/env python3
"""Check the notification channel is correctly configured -- without sending.

WHY THIS EXISTS SEPARATELY FROM THE VERIFICATION SUITE

`scripts/verification/moderation.py` checks what /api/verification/self says
about delivery, from outside, with no credential. It therefore cannot see the
environment variables themselves, which is the point -- that endpoint must
never expose them. But the state most likely to be believed is
half-configured: STREAKFIT_NOTIFY_CHANNEL=resend is set, somebody remembers
setting it, and the API key beside it is absent or wrong. The app already
refuses to treat that as capability. This tells an operator about it BEFORE
the deploy rather than after the first undelivered child-safety alert.

WHAT IT WILL NOT DO

  * It never prints a variable's VALUE. Not the API key, not truncated, not
    a prefix. Only names, and whether each is set.
  * It sends NO EMAIL and makes NO network request by default. The default
    run is entirely offline.
  * `--live` makes ONE read-only request to Resend's /domains endpoint, which
    lists sending domains and delivers nothing. It is opt-in per run, it
    prints the sending-domain verification state, and it never posts to
    /emails. Nothing in this file can send mail.

Exit codes: 0 ready, 1 a problem was found, 2 nothing is configured.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

REQUIRED = {
    'resend': ('RESEND_API_KEY', 'STREAKFIT_NOTIFY_FROM', 'STREAKFIT_NOTIFY_TO'),
    'console': (),
}

# Enough to catch a pasted placeholder or a swapped variable. Deliberately not
# strict RFC 5322 -- a real address this rejects would be a worse outcome than
# a malformed one it lets through, because the app fails loudly on send and
# silently on nothing.
# Mirrors app._NOTIFY_USER_AGENT. Cloudflare fronts api.resend.com and refuses
# urllib's default signature with a 1010 before Resend sees it, so a preflight
# without this reports a CDN block as a provider failure.
USER_AGENT = 'StreakFit/1.0 (+https://streakfit.pro)'

ADDRESS = re.compile(r'^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$')
NAMED_ADDRESS = re.compile(r'^.+<\s*([^@\s<>]+@[^@\s<>]+\.[^@\s<>]+)\s*>$')

problems = []
notes = []


def bare_address(value):
    """Resend accepts `Name <a@b.c>` for `from`. Pull out the address part."""
    m = NAMED_ADDRESS.match(value)
    return m.group(1) if m else value


def check(label, ok, detail_ok, detail_bad):
    print(f"  {'PASS' if ok else 'FAIL'}  {label:<34} {detail_ok if ok else detail_bad}")
    if not ok:
        problems.append(label)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--live', action='store_true',
                    help='additionally make ONE read-only call to Resend to confirm '
                         'the key is accepted and the sending domain is verified. '
                         'Sends no email.')
    args = ap.parse_args()

    channel = (os.environ.get('STREAKFIT_NOTIFY_CHANNEL') or '').strip()
    print("\nNotification preflight — values are never displayed, only names.\n")

    if not channel:
        print("  STREAKFIT_NOTIFY_CHANNEL is not set.\n")
        print("  Nothing is configured, so nothing will be delivered. That is a")
        print("  valid state and the app reports it honestly; it is not a")
        print("  failure of this check. Set it to 'resend' when ready.\n")
        return 2

    if channel not in REQUIRED:
        print(f"  FAIL  STREAKFIT_NOTIFY_CHANNEL names {channel!r}, which this "
              f"build does not know.")
        print(f"        Known channels: {', '.join(sorted(REQUIRED))}\n")
        return 1

    print(f"  channel: {channel}\n")

    present = {}
    for var in REQUIRED[channel]:
        value = (os.environ.get(var) or '').strip()
        present[var] = value
        check(f"{var} is set", bool(value), "set", "MISSING")

    if channel == 'console':
        print("\n  'console' logs instead of delivering. Fine for a rehearsal;")
        print("  it is not delivery, and the self-check will keep saying so.\n")
        return 0

    if problems:
        print("\n  Half-configured is NOT configured. The app will refuse to")
        print("  build this channel and will report every notice undelivered,")
        print("  which is correct -- but nothing will reach you until the")
        print("  variables above are set.\n")
        return 1

    # ── Shape checks. Offline; nothing leaves the machine. ──────────────────
    print()
    key = present['RESEND_API_KEY']
    check("RESEND_API_KEY looks like a Resend key",
          key.startswith('re_'),
          "starts with the expected prefix",
          "does NOT start with 're_' — likely the wrong secret pasted in")
    check("RESEND_API_KEY has no stray whitespace",
          key == key.strip() and ' ' not in key and '\n' not in key,
          "clean",
          "contains whitespace — a copy/paste artefact that will 401")

    sender = present['STREAKFIT_NOTIFY_FROM']
    check("STREAKFIT_NOTIFY_FROM parses as an address",
          bool(ADDRESS.match(bare_address(sender))),
          "well-formed",
          "not a usable address (expected a@b.c or 'Name <a@b.c>')")

    recipient = present['STREAKFIT_NOTIFY_TO']
    check("STREAKFIT_NOTIFY_TO parses as an address",
          bool(ADDRESS.match(bare_address(recipient))),
          "well-formed",
          "not a usable address")
    check("STREAKFIT_NOTIFY_TO is a single address",
          ',' not in recipient and ';' not in recipient,
          "one recipient",
          "looks like a list — the channel sends to exactly one address and "
          "a list will be treated as one malformed one")

    if problems:
        print(f"\n  {len(problems)} problem(s). Not ready.\n")
        return 1

    print("\n  Configuration is well-formed.\n")

    if not args.live:
        print("  NOT VERIFIED AGAINST RESEND. This run was entirely offline, so")
        print("  it cannot tell you the key is valid or the sending domain is")
        print("  verified — only that nothing is obviously malformed.")
        print("  Re-run with --live to check those. It still sends no email.\n")
        return 0

    # ── The one optional network call. Read-only; delivers nothing. ─────────
    print("  --live: asking Resend to list sending domains (no email sent)...")
    req = urllib.request.Request(
        'https://api.resend.com/domains',
        headers={'Authorization': f'Bearer {key}',
                 'Accept': 'application/json',
                 # Required, not decorative. Cloudflare fronts api.resend.com
                 # and refuses urllib's default `Python-urllib/3.x` with a
                 # 1010 before Resend ever sees the request. Same header the
                 # app sends (app._NOTIFY_USER_AGENT).
                 'User-Agent': USER_AGENT},
        method='GET')
    raw = ''
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            body = json.loads(raw or '{}')
            status = resp.status
    except urllib.error.HTTPError as exc:
        status, body = exc.code, {}
        try:
            raw = exc.read().decode('utf-8', 'replace')
        except Exception:
            raw = ''
    except Exception as exc:
        # Type only: a socket error can carry a hostname.
        print(f"  FAIL  could not reach Resend ({type(exc).__name__})\n")
        return 1

    if status == 403 and '1010' in raw:
        print("  FAIL  blocked by Cloudflare before reaching Resend (error 1010).")
        print("        The request never got to the provider. This is a client")
        print("        signature problem, not a credential problem.\n")
        return 1

    # A SENDING-ONLY KEY IS THE RIGHT KEY, and it cannot read /domains.
    #
    # Resend keys are scoped. A key restricted to sending is the correct least
    # privilege for this app -- it posts to /emails and needs nothing else --
    # and it answers 401 here saying exactly that. Reporting "Resend rejected
    # the API key" for a key that is valid, correctly scoped, and able to do
    # the one thing the app asks of it would be a false alarm that pushes an
    # operator toward a MORE privileged key. So it is read and named.
    if status == 401 and 'restricted to only send' in raw.lower():
        print("  PASS  Resend accepted the API key")
        print("  ----  it is a SENDING-ONLY key, so the sending-domain check")
        print("        below cannot run -- /domains needs broader access.")
        print()
        print("  Least privilege, and correct for this app: it only ever posts")
        print("  to /emails. Nothing here is wrong. What it does mean is that")
        print("  the sender can only be confirmed by sending -- see")
        print("  scripts/notification_live_send.py.\n")
        return 0

    if status == 401:
        print("  FAIL  Resend rejected the API key (401).\n")
        return 1
    if not (200 <= status < 300):
        print(f"  FAIL  Resend returned HTTP {status}.\n")
        return 1
    print("  PASS  Resend accepted the API key")

    domains = body.get('data') or []
    sender_domain = bare_address(sender).rsplit('@', 1)[-1].lower()

    # Resend's own test sender is not a domain you register, so the check
    # below would fail it — and it is the sender the rollout deliberately
    # uses for the first end-to-end test, before any DNS exists. See
    # docs/operations/notification-rollout.md stage 4.
    if sender_domain == 'resend.dev':
        print("  PASS  sending domain resend.dev (Resend's built-in test sender)")
        print()
        print("  Ready for the pre-DNS delivery test. Note the limitation this")
        print("  sender carries: it delivers ONLY to the Resend account owner's")
        print("  own address. If STREAKFIT_NOTIFY_TO is anyone else, the send")
        print("  will be rejected and no alert will arrive.")
        print("  No email was sent by this check.")
        print()
        return 0
    match = next((d for d in domains
                  if (d.get('name') or '').lower() == sender_domain), None)

    if match is None:
        print("  FAIL  no sending domain registered for the STREAKFIT_NOTIFY_FROM "
              "domain")
        print(f"        registered: {', '.join(d.get('name','?') for d in domains) or '(none)'}")
        print("\n  Resend will reject sends from an unregistered domain. On the")
        print("  free tier you can instead send from Resend's own test sender")
        print("  to your own address — see docs/operations/notification-rollout.md.\n")
        return 1

    verified = (match.get('status') or '').lower() == 'verified'
    print(f"  {'PASS' if verified else 'FAIL'}  sending domain {sender_domain} "
          f"status: {match.get('status')}")
    if not verified:
        print("\n  An unverified domain will not deliver. Finish DNS verification\n"
              "  in the Resend dashboard first.\n")
        return 1

    print("\n  Ready. The key is accepted and the sending domain is verified.")
    print("  No email was sent by this check — the first real message will be")
    print("  the first real alert.\n")
    return 0


if __name__ == '__main__':
    sys.exit(main())
