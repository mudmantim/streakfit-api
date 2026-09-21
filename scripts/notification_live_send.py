#!/usr/bin/env python3
"""Send ONE real alert through the real provider, deliberately, before any of
this is public.

WHY THIS IS A SEPARATE SCRIPT WITH A CONFIRMATION PROMPT

Everything else about delivery is now proven without touching a provider:
policy against fake channels, and the whole chain — notice, claim, a real HTTP
request with its Authorization and Idempotency-Key headers, the response
parsed, the receipt stored — over a real socket in
tests/test_delivery_end_to_end.py.

What none of that proves is that Resend accepts our request and an email
reaches a human. Only a real send does, and it is the last untested link
before the reporting UI becomes publicly reachable. The owner's requirement is
that the UI must not go live with *untested* notification delivery, so this
closes it BEFORE the deploy rather than after.

It runs locally. It does not need production, does not touch the production
database, and sends nothing to any address except STREAKFIT_NOTIFY_TO, which
is configuration and can never be influenced by a report or a user.

WHAT IT SENDS
  A real alert built by the real _notice_message(), for a clearly-labelled
  synthetic notice. The body carries no content, no evidence and nobody's
  name by construction — that is the point of the message format, and this
  exercises it rather than describing it.

WHAT IT NEVER DOES
  Print the API key, or any part of it. Send to anyone but the configured
  recipient. Run without an explicit confirmation typed at the prompt.

USAGE — the key goes in as INPUT to a running command, never as an argument
(arguments are visible in `ps`) and never as a history entry (this machine has
HISTCONTROL=ignoredups, so a leading space would not have helped):

  read -rsp 'Resend API key (hidden): ' RESEND_API_KEY; echo
  export RESEND_API_KEY
  export STREAKFIT_NOTIFY_FROM='onboarding@resend.dev'
  export STREAKFIT_NOTIFY_TO='you@example.com'
  export STREAKFIT_PUBLIC_URL='https://streakfit.pro'
  python scripts/notification_live_send.py
  unset RESEND_API_KEY
"""
import argparse
import os
import sys
import uuid
from datetime import datetime

os.environ.setdefault('SECRET_KEY', 'live-send-local-only')
os.environ.setdefault('JWT_SECRET_KEY', 'live-send-local-only')
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--yes', action='store_true',
                    help='skip the confirmation prompt (for a scripted run)')
    args = ap.parse_args()

    import logging
    logging.disable(logging.CRITICAL)
    import app as appmod

    channel_name = (os.environ.get('STREAKFIT_NOTIFY_CHANNEL') or 'resend').strip()
    if channel_name != 'resend':
        print(f"\n  STREAKFIT_NOTIFY_CHANNEL is {channel_name!r}. This script exists "
              f"to exercise the real provider; set it to 'resend'.\n")
        return 2

    # Build the channel first: it validates configuration and raises with
    # variable NAMES only if something is missing.
    try:
        channel = appmod.ResendChannel()
    except appmod.NotificationConfigError as exc:
        print(f"\n  Not configured: {exc}\n")
        print("  Run `python scripts/notification_preflight.py` first — it checks")
        print("  the same things offline and prints no values.\n")
        return 1

    recipient = channel.recipient
    sender = channel.sender

    # A synthetic notice, labelled as one. Real shape, real message builder.
    notice = appmod.ModerationNotice(
        subject_type='report',
        subject_ref='LIVE-SEND-TEST-' + uuid.uuid4().hex[:12],
        kind='urgent_filed',
        created_at=datetime.utcnow())
    subject, body = appmod._notice_message(notice)

    print("\n  About to send ONE REAL EMAIL through Resend.\n")
    print(f"    from      {sender}")
    print(f"    to        {recipient}      (from configuration, never from data)")
    print(f"    subject   {subject}")
    print(f"    endpoint  {appmod.ResendChannel.endpoint}")
    print("\n  Body, in full — note it carries no case content and nobody's name:\n")
    for line in body.splitlines():
        print(f"    | {line}")
    if sender.split('@')[-1].lower() == 'resend.dev':
        print("\n  NOTE: onboarding@resend.dev delivers ONLY to the Resend account")
        print("  owner's own address. If the recipient above is anyone else, Resend")
        print("  will reject it and no email will arrive.")

    if not args.yes:
        print()
        try:
            answer = input("  Type SEND to send it, anything else to cancel: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled. Nothing was sent.\n")
            return 1
        if answer != 'SEND':
            print("\n  Cancelled. Nothing was sent.\n")
            return 1

    key = uuid.uuid4().hex
    print("\n  Sending...")
    try:
        receipt = channel.send(subject, body, idempotency_key=key)
    except appmod.NotificationError as exc:
        # The adapter already reduces provider errors to a type, deliberately:
        # a provider error can echo the request back.
        print(f"\n  FAILED: {exc}\n")
        print("  The request was made and the provider did not accept it. Common")
        print("  causes: an unverified sending domain, or onboarding@resend.dev")
        print("  addressed to someone other than the account owner.\n")
        return 1

    print(f"\n  SENT. Provider receipt: {receipt}")
    print(f"  Idempotency-Key used:   {key}")
    print("\n  Now go and confirm it actually ARRIVED. A receipt means Resend")
    print("  accepted it for delivery, which is not the same as an email in an")
    print("  inbox — that last step is the one a person has to check.\n")
    return 0


if __name__ == '__main__':
    sys.exit(main())
