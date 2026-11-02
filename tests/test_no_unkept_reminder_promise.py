"""The app must not ask for notification permission it has no use for.

Roadmap A9 (2026-10-26): a banner asked "Would you like a daily reminder for
your mission?" and requested notification permission, but nothing ever sent a
reminder -- no push subscription, no service-worker `push` handler, no sender.
The ask was removed. This pins the rule so the promise cannot come back on its
own: the client may ask for permission (or offer a reminder) only once the
service worker can actually receive a pushed reminder.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
SW_JS = (ROOT / 'static' / 'sw.js').read_text(encoding='utf-8')
INDEX = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')

_SW_CAN_RECEIVE_PUSH = re.search(r"addEventListener\(\s*['\"]push['\"]", SW_JS) is not None


def _code_only(js):
    """app.js without // and /* */ comments, so an explanation is not a call."""
    js = re.sub(r'/\*.*?\*/', '', js, flags=re.S)
    return re.sub(r'(^|[^:])//[^\n]*', r'\1', js)


def test_no_permission_request_without_a_way_to_deliver_reminders():
    if _SW_CAN_RECEIVE_PUSH:
        return  # delivery exists (roadmap B4); asking is honest again
    assert 'requestPermission' not in _code_only(APP_JS)


def test_no_reminder_offer_in_the_ui_without_delivery():
    if _SW_CAN_RECEIVE_PUSH:
        return
    offer = re.compile(r'daily reminder|remind you|reminder for your mission', re.I)
    assert not offer.search(_code_only(APP_JS))
    assert not offer.search(re.sub(r'<!--.*?-->', '', INDEX, flags=re.S))


def test_the_guard_recognises_a_permission_request():
    # The check above is only worth having if it would catch the old banner.
    old = "yesBtn.onclick = function () { Notification.requestPermission().then(f); };"
    assert 'requestPermission' in _code_only(old)
    assert 'requestPermission' not in _code_only('// Notification.requestPermission() was here')
