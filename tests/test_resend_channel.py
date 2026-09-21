"""The Resend adapter, exercised against a fake provider.

NO NETWORK, NO ACCOUNT, NO REAL EMAIL. Every test here stubs the single HTTP
seam (`_notify_http_post`), which is the reason that seam exists: the adapter
is the one piece of this subsystem that cannot be proven by reading it, and it
is also the one piece nobody wants to prove by sending real mail to a real
person about a real child-safety report.

What these tests defend, in order of how badly it would go wrong:

  * the recipient is CONFIGURATION and can never become data,
  * a failure is never recorded as a delivery,
  * the credential never appears in a payload, an exception, or a log line,
  * the idempotency key is stable across retries of the same notice.
"""
import json

import pytest

import app as appmod
from app import (ResendChannel, NotificationError, NotificationConfigError,
                 _notification_channel, _channel_configuration_problem,
                 _delivery_capability, ModerationNotice, db,
                 _deliver_pending_notices, _notice_message, SOURCE_THREAD)
from datetime import datetime


API_KEY = "re_test_key_do_not_use_0000000000"
SENDER = "alerts@alerts.streakfit.example"
RECIPIENT = "owner@example.com"


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("STREAKFIT_NOTIFY_CHANNEL", "resend")
    monkeypatch.setenv("RESEND_API_KEY", API_KEY)
    monkeypatch.setenv("STREAKFIT_NOTIFY_FROM", SENDER)
    monkeypatch.setenv("STREAKFIT_NOTIFY_TO", RECIPIENT)


class FakeProvider:
    """Stands in for api.resend.com. Records what it was asked to send."""

    def __init__(self, status=200, body=None, boom=None):
        self.status, self.body, self.boom = status, body, boom
        self.calls = []

    def __call__(self, url, payload, headers, timeout=10):
        self.calls.append({"url": url, "payload": payload,
                           "headers": headers, "timeout": timeout})
        if self.boom:
            raise self.boom
        return self.status, (self.body if self.body is not None
                             else {"id": f"msg_{len(self.calls)}"})


@pytest.fixture()
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(appmod, "_notify_http_post", fake)
    return fake


# ── The recipient is configuration, never data ──────────────────────────────

def test_the_recipient_comes_from_configuration_only(configured, provider):
    ResendChannel().send("subject", "body", idempotency_key="k1")

    assert provider.calls[0]["payload"]["to"] == [RECIPIENT]


def test_no_argument_to_send_can_change_who_receives_it(configured, provider):
    """`send` takes a subject and a body. There is no recipient parameter, so
    a bug in notice generation can send the wrong SENTENCE and still cannot
    send it to the wrong PERSON."""
    import inspect

    params = list(inspect.signature(ResendChannel.send).parameters)
    assert params == ["self", "subject", "body", "idempotency_key"], params

    # And a subject/body that look like addresses change nothing.
    ResendChannel().send("attacker@evil.example",
                         "To: attacker@evil.example\nBcc: other@evil.example",
                         idempotency_key="k")
    assert provider.calls[-1]["payload"]["to"] == [RECIPIENT]
    assert "bcc" not in provider.calls[-1]["payload"]
    assert "cc" not in provider.calls[-1]["payload"]


def test_the_sender_comes_from_configuration(configured, provider):
    ResendChannel().send("s", "b")
    assert provider.calls[0]["payload"]["from"] == SENDER


# ── Receipts: delivered only when the far side says so ──────────────────────

def test_a_message_id_is_returned_as_the_receipt(configured, provider):
    provider.body = {"id": "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"}
    assert ResendChannel().send("s", "b") == "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"


def test_a_non_2xx_is_a_failure_not_a_delivery(configured, provider):
    provider.status, provider.body = 422, {"message": "nope"}
    with pytest.raises(NotificationError):
        ResendChannel().send("s", "b")


def test_a_200_with_no_id_is_a_failure(configured, provider):
    """Accepted-looking with nothing to record is not a delivery. `delivered_at`
    is only ever set beside a receipt."""
    provider.body = {}
    with pytest.raises(NotificationError):
        ResendChannel().send("s", "b")


def test_a_transport_error_is_a_failure_carrying_only_a_type(configured, provider):
    provider.boom = OSError("connect to db-host.internal:5432 refused")
    with pytest.raises(NotificationError) as exc:
        ResendChannel().send("s", "b")
    assert "OSError" in str(exc.value)
    assert "db-host.internal" not in str(exc.value)


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 502, 503])
def test_every_error_status_raises_rather_than_returning(configured, provider, status):
    provider.status, provider.body = status, None
    with pytest.raises(NotificationError):
        ResendChannel().send("s", "b")


# ── The credential never travels anywhere it should not ─────────────────────

def test_the_api_key_is_a_header_and_never_a_payload_field(configured, provider):
    ResendChannel().send("s", "b")
    call = provider.calls[0]
    assert call["headers"]["Authorization"] == f"Bearer {API_KEY}"
    assert API_KEY not in json.dumps(call["payload"])


def test_the_api_key_never_appears_in_a_raised_error(configured, provider):
    provider.status, provider.body = 401, None
    with pytest.raises(NotificationError) as exc:
        ResendChannel().send("s", "b")
    assert API_KEY not in str(exc.value)
    assert "re_test" not in str(exc.value)


def test_a_configuration_problem_names_variables_never_values(monkeypatch):
    """This string is served by /api/verification/self, without a credential."""
    monkeypatch.setenv("STREAKFIT_NOTIFY_CHANNEL", "resend")
    monkeypatch.setenv("RESEND_API_KEY", API_KEY)
    monkeypatch.delenv("STREAKFIT_NOTIFY_FROM", raising=False)
    monkeypatch.delenv("STREAKFIT_NOTIFY_TO", raising=False)

    problem = _channel_configuration_problem()
    assert "STREAKFIT_NOTIFY_FROM" in problem
    assert "STREAKFIT_NOTIFY_TO" in problem
    assert API_KEY not in problem


# ── Idempotency ─────────────────────────────────────────────────────────────

def test_the_idempotency_key_is_sent_as_a_header(configured, provider):
    ResendChannel().send("s", "b", idempotency_key="abc123")
    assert provider.calls[0]["headers"]["Idempotency-Key"] == "abc123"


def test_no_key_means_no_header_rather_than_an_empty_one(configured, provider):
    ResendChannel().send("s", "b", idempotency_key=None)
    assert "Idempotency-Key" not in provider.calls[0]["headers"]


def test_a_retry_of_the_same_notice_reuses_the_key(client, configured, provider):
    """The crash-window guarantee, end to end through the real delivery loop."""
    provider.status, provider.body = 500, None          # fail the first attempt
    n = ModerationNotice(subject_type='report', subject_ref='r' * 32,
                         kind='urgent_filed', created_at=datetime.utcnow())
    db.session.add(n)
    db.session.commit()

    _deliver_pending_notices(source=SOURCE_THREAD)
    first_key = provider.calls[-1]["headers"]["Idempotency-Key"]

    provider.status, provider.body = 200, {"id": "ok"}   # provider recovers
    _deliver_pending_notices(source=SOURCE_THREAD,
                             now=datetime.utcnow().replace(microsecond=0))
    # The second attempt may be held by backoff; force the eligible retry.
    n.last_attempt_at = None
    db.session.commit()
    _deliver_pending_notices(source=SOURCE_THREAD)

    assert provider.calls[-1]["headers"]["Idempotency-Key"] == first_key
    assert n.delivered_at is not None
    assert n.receipt == "ok"


# ── Plain text only ─────────────────────────────────────────────────────────

def test_the_email_is_plain_text_with_no_html(configured, provider):
    """No HTML means no tracking pixel and no remote image, so nothing tells a
    third party when a child-safety alert was opened."""
    ResendChannel().send("s", "b")
    payload = provider.calls[0]["payload"]
    assert payload["text"] == "b"
    assert "html" not in payload


# ── The body still obeys the redaction rules, through the channel ───────────

def test_what_actually_reaches_the_provider_carries_no_case_detail(
        client, configured, provider, monkeypatch):
    monkeypatch.setenv("STREAKFIT_PUBLIC_URL", "https://streakfit.example")
    monkeypatch.setenv("ADMIN_SECRET", "super-secret-value")
    n = ModerationNotice(subject_type='report', subject_ref='a' * 32,
                         kind='urgent_filed', created_at=datetime.utcnow())
    db.session.add(n)
    db.session.commit()

    _deliver_pending_notices(source=SOURCE_THREAD)

    blob = json.dumps(provider.calls[-1]["payload"])
    assert "super-secret-value" not in blob
    assert API_KEY not in blob
    # The pointer is there; the case is not.
    assert "a" * 32 in blob
    assert "https://streakfit.example/admin" in blob


# ── Configuration: half-configured is not configured ────────────────────────

def test_all_three_variables_are_required(monkeypatch):
    monkeypatch.setenv("STREAKFIT_NOTIFY_CHANNEL", "resend")
    for missing in ("RESEND_API_KEY", "STREAKFIT_NOTIFY_FROM", "STREAKFIT_NOTIFY_TO"):
        monkeypatch.setenv("RESEND_API_KEY", API_KEY)
        monkeypatch.setenv("STREAKFIT_NOTIFY_FROM", SENDER)
        monkeypatch.setenv("STREAKFIT_NOTIFY_TO", RECIPIENT)
        monkeypatch.delenv(missing, raising=False)

        with pytest.raises(NotificationConfigError):
            ResendChannel()
        assert _notification_channel() is None, f"{missing} missing still built a channel"
        assert missing in _channel_configuration_problem()


def test_a_half_configured_channel_is_not_capability(client, monkeypatch):
    monkeypatch.setenv("STREAKFIT_NOTIFY_CHANNEL", "resend")
    monkeypatch.setenv("RESEND_API_KEY", API_KEY)
    monkeypatch.delenv("STREAKFIT_NOTIFY_TO", raising=False)
    monkeypatch.delenv("STREAKFIT_NOTIFY_FROM", raising=False)

    configured_flag, _, why = _delivery_capability()
    assert configured_flag is False
    assert "STREAKFIT_NOTIFY_TO" in why

    payload = client.get('/api/verification/self').get_json()
    check = {c['id']: c for c in payload['checks']}['moderation.delivery_configured']
    assert check['status'] == 'FAIL'
    assert "STREAKFIT_NOTIFY_TO" in check['observed']


def test_a_fully_configured_channel_resolves(configured):
    ch = _notification_channel()
    assert isinstance(ch, ResendChannel)
    assert ch.certifies_delivery is True
    assert _channel_configuration_problem() is None


def test_an_unknown_channel_name_says_so(monkeypatch):
    monkeypatch.setenv("STREAKFIT_NOTIFY_CHANNEL", "mailgun")
    assert _notification_channel() is None
    assert "mailgun" in _channel_configuration_problem()


def test_nothing_configured_is_not_a_configuration_problem(monkeypatch):
    """Unset is a different fact from misconfigured, and reads differently."""
    monkeypatch.delenv("STREAKFIT_NOTIFY_CHANNEL", raising=False)
    assert _channel_configuration_problem() is None
    assert _notification_channel() is None
