"""One password policy for every new password; Unicode without lockouts; the
blocklist and its provenance.

docs/security/password-policy.md and docs/security/password-blocklist.md.
Every value here is synthetic.
"""
import importlib.util
import os
import unicodedata

import pytest
from werkzeug.security import generate_password_hash

import app as appmod
from conftest import auth_headers

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NFC = unicodedata.normalize('NFC', 'Café-lantern-morning-9')
NFD = unicodedata.normalize('NFD', NFC)


def _login(client, username, password):
    return client.post('/api/login', json={'username': username, 'password': password})


def _legacy_user(username, raw_password):
    """An account whose hash predates normalisation: the RAW string was hashed."""
    u = appmod.User(username=username,
                    password_hash=generate_password_hash(raw_password, method='pbkdf2:sha256'))
    appmod.db.session.add(u)
    appmod.db.session.commit()
    return u


def _count_hashes(monkeypatch):
    calls = []
    real = appmod.check_password_hash
    monkeypatch.setattr(appmod, 'check_password_hash',
                        lambda h, p: calls.append(1) or real(h, p))
    return calls


# ── Review B3: the username rule is no longer plain containment ──────────────

@pytest.mark.parametrize('username,password,expected', [
    ('anna', 'Savannah-lantern-meadow', None),          # was rejected before the review
    ('mark', 'Bookmark-kettle-77-x', None),
    ('timothy_h', 'timothy_h', 'too_short'),
    ('timothy_hill', 'timothy_hill2026', 'username_based'),   # 12 of 16: mostly the name
    ('timothy_hill', 'TIMOTHY_HILL!!!!', 'username_based'),
    ('timothy_hill', 'timothy_hill rides the blue tram', None),   # 12 of 32
    ('abc', 'abc-is-a-short-name', None),               # names under 4 are not judged
])
def test_the_username_rule_rejects_passwords_that_are_the_name_not_ones_that_contain_it(
        app, username, password, expected):
    assert appmod._password_policy_problem(username, password) == expected


# ── Unicode: one password whatever the keyboard; nobody locked out ───────────

def test_nfc_and_nfd_are_the_same_password_for_a_new_account(client):
    assert NFC != NFD
    assert client.post('/api/register', json={'username': 'uni_new', 'password': NFD}).status_code == 201
    assert _login(client, 'uni_new', NFC).status_code == 200
    assert _login(client, 'uni_new', NFD).status_code == 200


def test_full_width_input_is_the_same_password(client):
    wide = 'Ｌａｎｔｅｒｎ-ｋｅｔｔｌｅ-９９'          # NFKC -> 'Lantern-kettle-99'
    assert client.post('/api/register', json={'username': 'uni_wide', 'password': wide}).status_code == 201
    assert _login(client, 'uni_wide', 'Lantern-kettle-99').status_code == 200


def test_a_legacy_nfd_account_signs_in_from_an_nfc_keyboard_and_is_upgraded_once(client):
    """The review's reproduction (A4): stored in one composition, typed in the
    other -> 401. Now it signs in, and the hash is rewritten in NFKC so the
    next sign-in costs one check."""
    u = _legacy_user('uni_legacy', NFD)
    before = u.password_hash
    assert _login(client, 'uni_legacy', NFC).status_code == 200
    appmod.db.session.expire_all()
    after = appmod.db.session.get(appmod.User, u.id)
    assert after.password_hash != before
    assert appmod.check_password_hash(after.password_hash, unicodedata.normalize('NFKC', NFC))
    assert after.token_version == 0                 # nothing about the password changed
    assert _login(client, 'uni_legacy', NFD).status_code == 200


@pytest.mark.parametrize('raw', ['Old8char', 'Ünïcødé-old', 'ｆｕｌｌ-width-old'])
def test_every_existing_password_still_signs_in_exactly_as_typed_before(client, raw):
    _legacy_user('uni_exact_' + str(abs(hash(raw)) % 10**6), raw)
    name = 'uni_exact_' + str(abs(hash(raw)) % 10**6)
    assert _login(client, name, raw).status_code == 200
    assert _login(client, name, raw).status_code == 200          # and after any upgrade


def test_the_upgrade_never_overwrites_a_password_changed_in_between(client):
    """Rehash is conditional on the hash still being the one verified, under
    the row lock: a change committed in between wins."""
    u = _legacy_user('uni_race', NFD)
    verified = u.password_hash
    u.password_hash = generate_password_hash('Changed-in-between-1', method='pbkdf2:sha256')
    appmod.db.session.commit()
    appmod._upgrade_password_hash(u.id, verified, NFD)
    appmod.db.session.expire_all()
    assert appmod.check_password_hash(appmod.db.session.get(appmod.User, u.id).password_hash,
                                      'Changed-in-between-1')


def test_an_ascii_password_costs_one_check_as_before(client, monkeypatch):
    client.post('/api/register', json={'username': 'uni_ascii', 'password': 'Lantern-kettle-ascii'})
    calls = _count_hashes(monkeypatch)
    assert _login(client, 'uni_ascii', 'Lantern-kettle-ascii').status_code == 200
    assert len(calls) == 1
    calls.clear()
    assert _login(client, 'uni_ascii', 'wrong-ascii-guess').status_code == 401
    assert len(calls) == 1


def test_a_non_ascii_guess_costs_at_most_four_checks_and_the_same_for_unknown_users(client, monkeypatch):
    client.post('/api/register', json={'username': 'uni_cost', 'password': NFC})
    calls = _count_hashes(monkeypatch)
    guess = 'Ｗｒｏｎｇ-guess-é'
    assert _login(client, 'uni_cost', guess).status_code == 401
    known = len(calls)
    calls.clear()
    assert _login(client, 'no_such_user_here', guess).status_code == 401
    assert 1 < known <= 4
    assert len(calls) == known                    # no username oracle in the timing


def test_deletion_accepts_a_legacy_password_typed_in_the_other_composition(client):
    _legacy_user('uni_delete', NFD)
    token = _login(client, 'uni_delete', NFD).get_json()['access_token']
    r = client.delete('/api/me', json={'password': NFC}, headers=auth_headers(token))
    assert r.status_code == 200


# ── The blocklist file and its provenance ────────────────────────────────────

def _builder():
    spec = importlib.util.spec_from_file_location(
        'build_password_blocklist', os.path.join(REPO, 'scripts', 'build_password_blocklist.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_shipped_blocklist_is_loaded_whole(app):
    path = os.path.join(REPO, 'content', 'security', 'common-passwords.txt')
    text = open(path, encoding='utf-8').read()
    header, _, body = text.partition('\n\n')
    entries = [line for line in body.split('\n') if line]
    assert len(entries) == len(set(entries)) == len(appmod._COMMON_PASSWORDS)
    assert f'Count: {len(entries)}' in header
    assert len(entries) > 5000
    # Every entry is in comparison form and could be a new password.
    for e in entries:
        assert e == appmod._password_compare_form(e)
        assert appmod.PASSWORD_MIN_LENGTH <= len(e) <= appmod.PASSWORD_MAX_LENGTH


def test_the_header_names_the_pinned_sources_the_builder_checks(app):
    b = _builder()
    header = open(os.path.join(REPO, 'content', 'security', 'common-passwords.txt'),
                  encoding='utf-8').read().partition('\n\n')[0]
    assert b.SOURCE_COMMIT in header and '(MIT licence)' in header
    for path, digest in b.PINNED.values():
        assert f'{path} sha256={digest}' in header


def test_an_entry_beginning_with_hash_is_an_entry_not_a_comment(tmp_path):
    f = tmp_path / 'list.txt'
    f.write_text('# header\n\n#notacommentatall-1\nlanternkettle-99x\n', encoding='utf-8')
    assert appmod._load_password_blocklist(str(f)) == {'#notacommentatall-1', 'lanternkettle-99x'}


@pytest.mark.parametrize('content', ['', '# header only, no separator\n', '# header\n\n\n'])
def test_a_missing_or_broken_blocklist_stops_the_app_rather_than_allowing_everything(tmp_path, content):
    f = tmp_path / 'list.txt'
    f.write_text(content, encoding='utf-8')
    with pytest.raises(RuntimeError):
        appmod._load_password_blocklist(str(f))
    with pytest.raises(OSError):
        appmod._load_password_blocklist(str(tmp_path / 'absent.txt'))


def test_the_builder_decodes_hashcat_hex_and_drops_what_cannot_be_typed():
    b = _builder()
    assert b.decode_line(b'$HEX[6c616e7465726e]') == 'lantern'
    assert b.decode_line(b'$hex[c3a9]') == 'é'
    assert b.decode_line(b'$hex[ff]') is None           # not UTF-8
    assert b.decode_line(b'$hex[zz]') is None
    assert b.decode_line(b'plain\r') == 'plain'
    data = b'short\nexactly-15-char\n$hex[4c414e5445524e2d4b4554544c452d3939]\n\xff\xfe\n'
    assert sorted(b.entries(data, 15)) == ['exactly-15-char', 'lantern-kettle-99']


def test_the_builder_refuses_a_source_whose_digest_is_not_pinned(tmp_path):
    b = _builder()
    bad = tmp_path / 'ncsc.txt'
    bad.write_bytes(b'password\n')
    assert b.main([str(bad), str(bad), '--out', str(tmp_path / 'out.txt')]) == 1
    assert not (tmp_path / 'out.txt').exists()
