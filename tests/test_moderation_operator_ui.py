"""The operator interface the moderation API shipped without.

THE DEFECT THESE EXIST FOR, found on production 2026-09-22 by trying to close
a real synthetic child-safety report and discovering it could not be done:

The moderation API was complete and correct. `/api/admin/reports`,
`/reports/<id>` and `/reports/<id>/action` all worked. The alert email said
"Open <url>/admin to review it". And `/admin` served a dashboard that called
six endpoints, none of them the reports API — so the reviewer arrived at a
page with no queue, no report, and no way to act. The report had to be closed
by assembling HTTP requests with a secret in a shell.

Every API test passed throughout. That is the point: a test suite that only
asks "does the endpoint respond" cannot see that nobody can reach it. This
project has shipped that exact failure before, in the R2 team layer, and it
was caught by a stability review rather than by tests.

So these assert the SHIPPED OPERATOR PAGE, not the API behind it.
"""
import re
from pathlib import Path

import pytest

import app as appmod

ADMIN = (Path(appmod.__file__).resolve().parent / 'static' / 'admin.html').read_text(encoding='utf-8')
SERVER = (Path(appmod.__file__).resolve().parent / 'app.py').read_text(encoding='utf-8')


def _inline_js():
    m = re.search(r'<script>(.*?)</script>', ADMIN, re.S)
    assert m, 'admin.html has no inline script'
    return m.group(1)


# ── The failure that actually happened ─────────────────────────────────────

def test_the_admin_page_calls_the_reports_api():
    """THE REGRESSION. The page called six admin endpoints and not this one,
    so a report could be alerted about and never actioned."""
    assert '/api/admin/reports' in ADMIN, (
        "the operator page does not call the reports API — the moderation "
        "queue is unreachable, exactly the state found on 2026-09-22")


def test_the_page_can_submit_a_disposition():
    js = _inline_js()
    assert '/action' in js and "method: 'POST'" in js, (
        "the page can read reports but not act on them")


def test_the_alert_email_link_and_the_queue_agree():
    """The email says "Open <url>/admin to review it". If the queue ever moves
    off /admin, that instruction becomes a dead end again — which is the
    original defect, not a new one."""
    assert 'f"{where}/admin"' in SERVER, "the alert link is no longer /admin"
    assert "@app.route('/admin')" in SERVER, "/admin is no longer served"
    assert 'id="moderation-queue"' in ADMIN, (
        "the page the alert points at has no moderation queue")


# ── Everything an operator must be able to do ──────────────────────────────

@pytest.mark.parametrize('what,needle', [
    ('see pending reports',        '?status='),
    ('see overdue reports',        "'overdue'"),
    ('see closed reports',         "'closed'"),
    ('see a deadline',             'due_at'),
    ('see urgency',                'urgent'),
    ('open one report',            'openModerationReport'),
    ('see previous actions',       'mod-history'),
    ('choose a disposition',       'mod-action-select'),
    ('write an audit note',        'mod-action-note'),
    ('submit it',                  'submitModerationAction'),
    ('see success or failure',     'mod-feedback'),
])
def test_the_operator_can(what, needle):
    assert needle in ADMIN, f"an operator cannot {what}"


def test_an_action_requires_an_audit_note():
    """The note is the audit trail. An action without one is a decision nobody
    can explain later."""
    js = _inline_js()
    m = re.search(r'function submitModerationAction\(.*?\n  \}', js, re.S)
    assert m, 'submitModerationAction not found'
    assert 'An audit note is required' in m.group(0), (
        "an action can be submitted with no note")


def test_an_action_is_confirmed_before_it_is_sent():
    """Several dispositions apply real sanctions to a real account, and a
    mis-click in a select is easy at speed.

    The confirmation is IN THE PAGE, not window.confirm(). A native dialog
    blocks the renderer, so it cannot be driven in a headless browser — the one
    safeguard between a mis-click and a real sanction would have been the one
    thing never tested. It also cannot be styled and reads badly on a phone.
    """
    js = _inline_js()
    assert 'window.confirm(' not in js.replace('// Not window.confirm()', ''), (
        "a blocking native dialog is back; it cannot be browser-tested")
    assert 'armedAction' in js, "there is no two-step confirmation"
    assert 'cancelModerationAction' in js, "a confirmation cannot be cancelled"


def test_cancelling_does_not_submit_anything():
    js = _inline_js()
    m = re.search(r'function cancelModerationAction\(\)\s*\{(.*?)\n  \}', js, re.S)
    assert m, 'cancelModerationAction not found'
    body = m.group(1)
    assert 'fetch(' not in body, "cancelling issues a request"
    assert 'armedAction = null' in body, "cancelling leaves the action armed"


def test_a_pending_confirmation_cannot_outlive_its_report():
    """Re-rendering the panel must disarm, or a confirmation shown for one
    report could be applied to whatever is displayed next."""
    js = _inline_js()
    m = re.search(r'function renderModerationReport\(', js)
    assert m, 'renderModerationReport not found'
    after = js[m.start():m.start() + 300]
    assert 'armedAction = null' in after, (
        "re-rendering does not clear a pending confirmation")


def test_the_result_is_verified_by_re_reading_the_report():
    """A 200 means the request was accepted, not that the status changed. The
    operator needs to see what is actually stored."""
    js = _inline_js()
    m = re.search(r'function submitModerationAction\(.*?\n  \}', js, re.S)
    assert 'apiGet(secret,' in m.group(0), (
        "the page reports success without re-reading the report")
    assert 'Status is now' in m.group(0), (
        "the operator is not shown the status that actually resulted")


# ── Authorization and disclosure ───────────────────────────────────────────

def test_every_moderation_call_is_authenticated():
    """Reads go through apiGet(), which sets the header. The action is a raw
    fetch(), so it is checked directly — an unauthenticated write would be the
    worse half to get wrong."""
    js = _inline_js()
    for f in re.findall(r"fetch\('/api/admin/reports[^;]*", js):
        assert 'X-Admin-Secret' in f, (
            "a moderation request is made without the admin secret header")
    for call in re.findall(r"apiGet\(([^,]+),\s*'/api/admin/reports", js):
        assert call.strip() == 'secret', (
            f"a moderation read passes {call!r} rather than the operator secret")


def test_the_secret_is_never_put_in_a_url():
    """A query string lands in server logs, proxy logs and browser history."""
    js = _inline_js()
    assert not re.search(r"reports\?[^']*secret", js, re.I), (
        "the admin secret appears in a URL")


def test_no_new_disposition_was_invented():
    """The UI must offer exactly what the server accepts — no more."""
    server_actions = set(re.findall(r"'([a-z_]+)'",
        re.search(r'MODERATION_ACTIONS = \((.*?)\)', SERVER, re.S).group(1)))
    # Scoped to the MOD_ACTIONS block. A loose match over the whole script
    # picked up an unrelated analytics array and reported "today" as an
    # invented disposition -- a test that cries wolf gets ignored.
    block = re.search(r'var MOD_ACTIONS = \[(.*?)\];', _inline_js(), re.S)
    assert block, 'MOD_ACTIONS not found in the operator page'
    ui_actions = set(re.findall(r"\['([a-z_]+)',", block.group(1)))
    invented = ui_actions - server_actions
    assert not invented, f"the UI offers dispositions the server does not accept: {invented}"


def test_report_fields_are_escaped_before_rendering():
    """Report content reaches this page from users. Rendered raw into innerHTML
    it would be script injection into the operator's own session."""
    js = _inline_js()
    assert 'function esc(' in js, 'no escaping helper'
    assert "replace(/</g, '&lt;')" in js, 'the escaper does not neutralise markup'
    assert 'esc(r.category)' in js, 'report fields are rendered without escaping'


# ── Usable on the device an operator actually has ──────────────────────────

def test_the_queue_is_usable_on_a_narrow_screen():
    assert '@media (max-width: 560px)' in ADMIN, (
        "no narrow-screen handling; a 2am page is often read on a phone")


def test_controls_are_comfortable_tap_targets():
    for cls in ('.mod-submit', '.mod-open', '#mod-action-select, #mod-action-note'):
        m = re.search(re.escape(cls) + r'[^{]*\{([^}]*)\}', ADMIN)
        assert m, f'no styles for {cls}'
        assert 'min-height:44px' in m.group(1).replace(' ', ''), (
            f'{cls} is smaller than the minimum comfortable tap target')
