"""The rollback build keeps PW-1's sign-up password policy.

A rollback must never lower the rules for new passwords (owner, 2026-10-12):
15+ characters, not mostly the username, not the service name, not repetitive
or sequential, not on the breached/common blocklist. Existing accounts and
passwords keep working exactly as before. Synthetic values only.
"""
import importlib.util
import os
import unicodedata

import pytest
from werkzeug.security import generate_password_hash

import app as appmod
from conftest import auth_headers

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _login(client, username, password):
    return client.post('/api/login', json={'username': username, 'password': password})


@pytest.mark.parametrize('name,pw,code', [
    ('rb_short', 'Fourteen-chars', 'too_short'),
    ('rb_common', 'passwordpassword', 'common'),
    ('rb_common_case', 'PasswordPassword', 'common'),
    ('rb_seq', 'abcdefghijklmnop', 'sequential'),
    ('rb_rep', 'aaaaaaaaaaaaaaaa', 'repetitive'),
    ('rb_mostly_name', 'rb_mostly_name-1', 'username_based'),
    ('rb_service', 'StreakFit-2026-!!!', 'service_name'),
])
def test_sign_up_refuses_what_pw1_refuses(client, name, pw, code):
    r = client.post('/api/register', json={'username': name, 'password': pw})
    assert r.status_code == 400
    assert r.get_json()['code'] == code
    assert r.get_json()['error'] == appmod._PASSWORD_POLICY_MESSAGES[code]


def test_a_compliant_password_signs_up_and_in(client):
    assert client.post('/api/register', json={'username': 'rb_ok', 'password': 'Fifteen-chars-ok'}).status_code == 201
    assert _login(client, 'rb_ok', 'Fifteen-chars-ok').status_code == 200


def test_anna_inside_savannah_is_not_refused(client):
    assert appmod._password_policy_problem('anna', 'Savannah-lantern-meadow') is None


def test_sign_up_stores_nfkc_so_any_keyboard_signs_in(client):
    wide = 'Ｌａｎｔｅｒｎ-ｋｅｔｔｌｅ-９９'
    assert client.post('/api/register', json={'username': 'rb_wide', 'password': wide}).status_code == 201
    assert _login(client, 'rb_wide', 'Lantern-kettle-99').status_code == 200
    nfc = unicodedata.normalize('NFC', 'Café-lantern-morning-9')
    assert client.post('/api/register', json={'username': 'rb_nfd',
                                              'password': unicodedata.normalize('NFD', nfc)}).status_code == 201
    assert _login(client, 'rb_nfd', nfc).status_code == 200


@pytest.mark.parametrize('legacy', ['Old8char', 'Ünïcødé-old', 'ｆｕｌｌ-width-old'])
def test_an_existing_password_under_the_new_minimum_still_signs_in_and_deletes(client, legacy):
    u = appmod.User(username='rb_legacy', password_hash=generate_password_hash(legacy, method='pbkdf2:sha256'))
    appmod.db.session.add(u)
    appmod.db.session.commit()
    r = _login(client, 'rb_legacy', legacy)
    assert r.status_code == 200
    assert client.delete('/api/me', json={'password': legacy},
                         headers=auth_headers(r.get_json()['access_token'])).status_code == 200


def test_the_shipped_blocklist_is_loaded_whole_and_names_its_sources(app):
    path = os.path.join(REPO, 'content', 'security', 'common-passwords.txt')
    header, _, body = open(path, encoding='utf-8').read().partition('\n\n')
    entries = [line for line in body.split('\n') if line]
    assert len(entries) == len(set(entries)) == len(appmod._COMMON_PASSWORDS) > 5000
    spec = importlib.util.spec_from_file_location(
        'build_password_blocklist', os.path.join(REPO, 'scripts', 'build_password_blocklist.py'))
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    assert b.SOURCE_COMMIT in header
    for p, digest in b.PINNED.values():
        assert f'{p} sha256={digest}' in header


@pytest.mark.parametrize('content', ['', '# header only\n', '# header\n\n\n'])
def test_a_broken_blocklist_stops_the_app_rather_than_allowing_everything(tmp_path, content):
    f = tmp_path / 'list.txt'
    f.write_text(content, encoding='utf-8')
    with pytest.raises(RuntimeError):
        appmod._load_password_blocklist(str(f))
