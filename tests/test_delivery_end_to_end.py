"""Delivery over a real socket, from a filed report to a recorded receipt.

WHY THIS EXISTS ALONGSIDE test_notification_delivery.py

That file polices delivery POLICY with fake channels, deliberately: the
interface exists separately from any provider so the retry, lease, priority
and honesty rules can be tested without a network. It stubs
`_notify_http_post`. So does test_resend_channel.py -- it says so in its own
docstring, and for good reason.

But that means the one function that actually talks to the outside world had
never been executed against a socket. Everything around it was proven and the
step in the middle was assumed. The parts only a real request exercises:

  * json.dumps of the payload and the Content-Type header
  * Authorization and Idempotency-Key surviving as real HTTP headers
  * urllib actually connecting, and the response being read and decoded
  * the provider's JSON id being parsed back out as the receipt
  * that receipt reaching the database beside `delivered_at`

This test runs a real HTTP server on localhost that answers like Resend's
/emails endpoint, points the real ResendChannel at it, and drives the whole
chain: file a report through the API, generate notices, run the delivery
worker, then read the server's recorded request and the database row.

WHAT IT DOES NOT PROVE, and must not be quoted as proving:

  * It is HTTP on localhost, not TLS to api.resend.com. Certificate handling
    and DNS are not exercised.
  * The stub is written to Resend's documented shape, which is not the same as
    Resend's behaviour.
  * Nothing here shows an email arrives in a human's inbox. That needs a real
    account and one real send, and remains the last untested link.

It closes the gap between "our code is correct in isolation" and "our code
makes a well-formed request" -- not the gap to the provider.
"""
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import auth_headers, register_and_login

import app as appmod
from app import (db, ModerationNotice, ResendChannel, SOURCE_THREAD,
                 _deliver_pending_notices, _generate_moderation_notices)


class RecordingResend(BaseHTTPRequestHandler):
    """Answers like Resend's POST /emails and keeps what it was sent."""

    received = []
    status = 200
    body = {"id": "re_stub_0000000000"}

    def do_POST(self):                                   # noqa: N802
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw or b'{}')
        except ValueError:
            payload = None
        type(self).received.append({
            'path': self.path,
            'headers': {k.lower(): v for k, v in self.headers.items()},
            'payload': payload,
        })
        out = json.dumps(type(self).body).encode('utf-8')
        self.send_response(type(self).status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):                        # keep pytest output clean
        pass


@pytest.fixture
def resend_stub(monkeypatch):
    """A real server on a real port, with the real channel pointed at it."""
    RecordingResend.received = []
    RecordingResend.status = 200
    RecordingResend.body = {"id": "re_stub_0000000000"}

    server = HTTPServer(('127.0.0.1', 0), RecordingResend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    monkeypatch.setenv('RESEND_API_KEY', 're_test_key_not_a_real_one')
    monkeypatch.setenv('STREAKFIT_NOTIFY_FROM', 'alerts@example.com')
    monkeypatch.setenv('STREAKFIT_NOTIFY_TO', 'tim@example.com')
    monkeypatch.setattr(ResendChannel, 'endpoint', f'http://{host}:{port}/emails')

    try:
        yield RecordingResend
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _queued_urgent_notice():
    n = ModerationNotice(subject_type='report', subject_ref='e' * 32,
                         kind='urgent_filed', created_at=datetime.utcnow())
    db.session.add(n)
    db.session.commit()
    return n


# ── The whole chain, over a socket ─────────────────────────────────────────

def test_a_notice_becomes_a_real_http_request_and_a_recorded_receipt(
        client, resend_stub, monkeypatch):
    notice = _queued_urgent_notice()
    monkeypatch.setattr(appmod, '_notification_channel',
                        lambda name=None: ResendChannel())

    _deliver_pending_notices(source=SOURCE_THREAD)

    assert len(resend_stub.received) == 1, (
        f"expected exactly one HTTP request, got {len(resend_stub.received)}")
    sent = resend_stub.received[0]

    assert sent['path'] == '/emails'
    assert sent['headers'].get('content-type') == 'application/json'
    assert sent['headers'].get('authorization') == 'Bearer re_test_key_not_a_real_one'

    payload = sent['payload']
    assert payload['from'] == 'alerts@example.com'
    assert payload['to'] == ['tim@example.com'], "recipient must be a list of one"
    assert payload['subject'] and payload['text']
    assert 'html' not in payload, (
        "plain text only -- HTML would allow a tracking pixel that reveals when "
        "a child-safety alert was opened")

    db.session.refresh(notice)
    assert notice.delivered_at is not None
    assert notice.receipt == 're_stub_0000000000', (
        "the provider's id must be parsed off the real response and stored")


def test_the_idempotency_key_is_sent_as_a_header(client, resend_stub, monkeypatch):
    """It is the only thing standing between a crash mid-send and a duplicate
    child-safety alert, and it only works if it survives as a real header."""
    notice = _queued_urgent_notice()
    monkeypatch.setattr(appmod, '_notification_channel',
                        lambda name=None: ResendChannel())

    _deliver_pending_notices(source=SOURCE_THREAD)

    key = resend_stub.received[0]['headers'].get('idempotency-key')
    assert key, "no Idempotency-Key header reached the server"
    db.session.refresh(notice)
    assert key == str(notice.provider_key), (
        "the header must be the key committed before the send, or a retry "
        "after a crash would not deduplicate")


@pytest.fixture
def team(client):
    """Two people in a team. Built here rather than imported, because the
    fixture in test_moderation_operations.py is local to that file."""
    owner = register_and_login(client, 'e2eowner')
    member = register_and_login(client, 'e2emember')
    t = client.post('/api/teams', json={'name': 'Delivery E2E'},
                    headers=auth_headers(owner)).get_json()['team']
    client.post(f'/api/teams/{t["id"]}/join', json={'code': t['invite_code']},
                headers=auth_headers(member))
    return owner, member, t


def test_a_report_filed_through_the_api_reaches_the_provider(
        client, team, resend_stub, monkeypatch):
    """The full path a real incident takes, with nothing hand-inserted."""
    owner, member, t = team
    monkeypatch.setattr(appmod, '_notification_channel',
                        lambda name=None: ResendChannel())

    r = client.post('/api/reports', json={
        'category': 'child_safety', 'subject_type': 'user',
        'reported_user_id': _uid(client, owner), 'team_id': t['id']},
        headers=_auth(member))
    assert r.status_code == 201, r.get_data(as_text=True)

    _generate_moderation_notices()
    _deliver_pending_notices(source=SOURCE_THREAD)

    assert resend_stub.received, "a filed child_safety report sent no alert"
    text = resend_stub.received[0]['payload']['text']
    subject = resend_stub.received[0]['payload']['subject']
    assert 'StreakFit' in subject

    # The alert must not carry identifiers. It tells a human to go and look.
    assert 'timhill' not in text.lower()
    assert _login(client, member).lower() not in text.lower()


def test_a_provider_rejection_is_not_recorded_as_delivered(
        client, resend_stub, monkeypatch):
    """A 4xx over a real socket must leave the notice undelivered and
    retryable, not marked done because the request completed."""
    resend_stub.status = 422
    resend_stub.body = {"message": "domain not verified"}
    notice = _queued_urgent_notice()
    monkeypatch.setattr(appmod, '_notification_channel',
                        lambda name=None: ResendChannel())

    _deliver_pending_notices(source=SOURCE_THREAD)

    assert resend_stub.received, "the request was never made"
    db.session.refresh(notice)
    assert notice.delivered_at is None, "a 422 was recorded as a delivery"
    assert notice.receipt is None


def test_an_accepted_response_with_no_id_is_not_a_delivery(
        client, resend_stub, monkeypatch):
    """200 with no message id is not something we can record, and
    `delivered_at` is only ever set beside a receipt."""
    resend_stub.body = {}
    notice = _queued_urgent_notice()
    monkeypatch.setattr(appmod, '_notification_channel',
                        lambda name=None: ResendChannel())

    _deliver_pending_notices(source=SOURCE_THREAD)

    db.session.refresh(notice)
    assert notice.delivered_at is None
    assert notice.receipt is None


# ── helpers ────────────────────────────────────────────────────────────────

def _auth(token):
    return auth_headers(token)


def _uid(client, token):
    return client.get('/api/me', headers=_auth(token)).get_json()['id']


def _login(client, token):
    return client.get('/api/me', headers=_auth(token)).get_json()['username']
