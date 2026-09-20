"""Default deny, and the things that must never be able to override it.

These are the foundations, not the features. No route consults `can()` yet,
no age screen exists, and nothing writes a GuardianLink — deliberately, because
what counts as a compliant neutral age screen and what a guardian may see are
open questions (docs/child-safety/architecture.md).

What IS decided, and is therefore tested: absence of consent is denial, an
unestablished age is treated as a child, money buys allowances and never age,
and a capability with an unmet external condition cannot be consented into.

No real child's information appears anywhere here. Every fixture is synthetic.
"""
import pytest

import app as appmod
from app import Consent, GuardianLink, PermissionAudit, User, db


# ── Synthetic fixtures, one per age shape ──────────────────────────────────

def _user(band, username, plus=False):
    u = User(username=username, password_hash='x', age_band=band, is_plus=plus)
    db.session.add(u)
    db.session.commit()
    return u


def _link(child, guardian, method="synthetic-test", revoked=False):
    gl = GuardianLink(child_user_id=child.id, guardian_user_id=guardian.id,
                      method=method)
    if revoked:
        gl.revoked_at = appmod.datetime.utcnow()
    db.session.add(gl)
    db.session.commit()
    return gl


def _consent(child, link, capability, revoked=False):
    c = Consent(child_user_id=child.id, guardian_link_id=link.id,
                capability=capability, method="synthetic-test")
    if revoked:
        c.revoked_at = appmod.datetime.utcnow()
    db.session.add(c)
    db.session.commit()
    return c


# ── Default deny ───────────────────────────────────────────────────────────

def test_a_child_with_no_guardian_consent_can_do_nothing_external(app):
    kid = _user(appmod.AGE_CHILD, "synth_kid_alone")
    for cap in (appmod.CAP_TEAMS, appmod.CAP_TEAM_CHAT, appmod.CAP_TEAM_PHOTOS):
        allowed, reason = appmod.can(kid, cap)
        assert allowed is False, cap
        assert reason == "no guardian consent", (cap, reason)


def test_an_unestablished_age_is_treated_as_a_child_not_an_adult(app):
    """NULL is not adult.

    Every existing account has a NULL band, so getting this backwards would
    silently grant every current user adult standing — and would do it by
    doing nothing, which is the hardest kind of bug to notice.
    """
    unknown = _user(None, "synth_unknown_age")
    allowed, reason = appmod.can(unknown, appmod.CAP_TEAM_CHAT)
    assert allowed is False
    assert reason == "age not established"


def test_an_adult_is_unaffected(app):
    """The foundations must not change what works today."""
    grown = _user(appmod.AGE_ADULT, "synth_adult")
    for cap in appmod.CAPABILITIES:
        allowed, reason = appmod.can(grown, cap)
        assert allowed is True, cap
        assert reason == "adult"


def test_consent_grants_exactly_one_capability_and_no_others(app):
    """Per capability, because "use teams" and "send free text to a third
    party model" are not the same decision."""
    kid = _user(appmod.AGE_CHILD, "synth_kid_teams")
    guardian = _user(appmod.AGE_ADULT, "synth_guardian_a")
    link = _link(kid, guardian)
    _consent(kid, link, appmod.CAP_TEAMS)

    assert appmod.can(kid, appmod.CAP_TEAMS)[0] is True
    assert appmod.can(kid, appmod.CAP_TEAM_CHAT)[0] is False
    assert appmod.can(kid, appmod.CAP_TEAM_PHOTOS)[0] is False


# ── Revocation ─────────────────────────────────────────────────────────────

def test_revoking_consent_takes_the_capability_away(app):
    kid = _user(appmod.AGE_CHILD, "synth_kid_revoke")
    guardian = _user(appmod.AGE_ADULT, "synth_guardian_b")
    link = _link(kid, guardian)
    consent = _consent(kid, link, appmod.CAP_TEAMS)
    assert appmod.can(kid, appmod.CAP_TEAMS)[0] is True

    consent.revoked_at = appmod.datetime.utcnow()
    db.session.commit()
    allowed, reason = appmod.can(kid, appmod.CAP_TEAMS)
    assert allowed is False
    assert reason == "no guardian consent"


def test_revoking_the_GUARDIAN_LINK_revokes_everything_it_granted(app):
    """A guardian who is no longer a guardian cannot leave permissions behind.

    The obvious bug is to check only the consent row. Then unlinking a
    guardian leaves every capability they granted still live, which is the
    exact situation a disputed or removed guardian creates.
    """
    kid = _user(appmod.AGE_CHILD, "synth_kid_unlink")
    guardian = _user(appmod.AGE_ADULT, "synth_guardian_c")
    link = _link(kid, guardian)
    for cap in (appmod.CAP_TEAMS, appmod.CAP_TEAM_CHAT):
        _consent(kid, link, cap)
    assert appmod.can(kid, appmod.CAP_TEAM_CHAT)[0] is True

    link.revoked_at = appmod.datetime.utcnow()
    db.session.commit()
    for cap in (appmod.CAP_TEAMS, appmod.CAP_TEAM_CHAT):
        assert appmod.can(kid, cap)[0] is False, cap


def test_a_second_guardians_consent_survives_the_first_being_revoked(app):
    """Multiple legitimate guardians are supported, so revoking one must not
    silently revoke the other's decisions."""
    kid = _user(appmod.AGE_CHILD, "synth_kid_two")
    mum = _user(appmod.AGE_ADULT, "synth_guardian_mum")
    dad = _user(appmod.AGE_ADULT, "synth_guardian_dad")
    mum_link, dad_link = _link(kid, mum), _link(kid, dad)
    _consent(kid, mum_link, appmod.CAP_TEAMS)
    _consent(kid, dad_link, appmod.CAP_TEAMS)

    mum_link.revoked_at = appmod.datetime.utcnow()
    db.session.commit()
    assert appmod.can(kid, appmod.CAP_TEAMS)[0] is True


# ── The things that must never work ────────────────────────────────────────

@pytest.mark.parametrize("cap", appmod.CAPABILITIES)
def test_paying_never_buys_age(app, cap):
    """Plus and sponsorship buy allowances. They do not buy age.

    This is the test the brief asked for by name, and it is worth having as a
    test rather than a convention: the tempting shortcut in every quota check
    is `if user.is_plus: allow`, and here that would hand a child every
    capability for $4.99.
    """
    kid = _user(appmod.AGE_CHILD, f"synth_kid_plus_{cap}", plus=True)
    allowed, _reason = appmod.can(kid, cap)
    assert allowed is False, f"a Plus subscription unlocked {cap} for a child"


def test_a_guardian_relationship_is_not_established_by_paying(app):
    """A sponsor is not a guardian. Nothing in `can()` reads a payment field,
    and nothing writes a GuardianLink from one."""
    kid = _user(appmod.AGE_CHILD, "synth_kid_sponsored")
    sponsor = _user(appmod.AGE_ADULT, "synth_sponsor", plus=True)
    # No link is created by any payment path — there is no such path.
    assert db.session.execute(
        db.select(GuardianLink).where(GuardianLink.child_user_id == kid.id)
    ).scalar_one_or_none() is None
    assert appmod.can(kid, appmod.CAP_TEAMS)[0] is False
    assert sponsor.is_plus is True


def test_a_hard_blocked_capability_cannot_be_consented_into(app):
    """The 312.8(c) gate.

    A guardian may consent to anything they like; it does not conjure a
    written assurance from the AI provider. The block is not about consent,
    so consent cannot lift it.
    """
    kid = _user(appmod.AGE_CHILD, "synth_kid_ai")
    guardian = _user(appmod.AGE_ADULT, "synth_guardian_ai")
    link = _link(kid, guardian)
    _consent(kid, link, appmod.CAP_ASK_RICKIE)

    allowed, reason = appmod.can(kid, appmod.CAP_ASK_RICKIE)
    assert allowed is False
    assert "assurance" in reason


def test_the_hard_block_opens_only_when_the_condition_is_declared(app, monkeypatch):
    """And it is a real gate, not decoration — it opens, which is how we know
    it is wired to something rather than hard-coded to refuse."""
    kid = _user(appmod.AGE_CHILD, "synth_kid_ai_ok")
    guardian = _user(appmod.AGE_ADULT, "synth_guardian_ai_ok")
    link = _link(kid, guardian)
    _consent(kid, link, appmod.CAP_ASK_RICKIE)
    assert appmod.can(kid, appmod.CAP_ASK_RICKIE)[0] is False

    monkeypatch.setenv("STREAKFIT_CHILD_AI_ASSURANCE_ON_FILE", "1")
    allowed, reason = appmod.can(kid, appmod.CAP_ASK_RICKIE)
    assert allowed is True
    assert reason == "guardian consent on file"


def test_consent_for_one_child_does_not_reach_another(app):
    """The obvious cross-account bug, asserted before any route exists."""
    kid_a = _user(appmod.AGE_CHILD, "synth_kid_x")
    kid_b = _user(appmod.AGE_CHILD, "synth_kid_y")
    guardian = _user(appmod.AGE_ADULT, "synth_guardian_x")
    link = _link(kid_a, guardian)
    _consent(kid_a, link, appmod.CAP_TEAMS)

    assert appmod.can(kid_a, appmod.CAP_TEAMS)[0] is True
    assert appmod.can(kid_b, appmod.CAP_TEAMS)[0] is False


def test_a_forged_consent_row_pointing_at_another_childs_link_does_not_work(app):
    """Defence in depth against a bad write.

    `can()` re-checks that the link belongs to this child rather than
    trusting the consent row's own child_user_id, so a row written with a
    mismatched pair grants nothing.
    """
    kid = _user(appmod.AGE_CHILD, "synth_kid_forge")
    other = _user(appmod.AGE_CHILD, "synth_kid_other")
    guardian = _user(appmod.AGE_ADULT, "synth_guardian_forge")
    other_link = _link(other, guardian)          # link belongs to `other`
    db.session.add(Consent(child_user_id=kid.id, guardian_link_id=other_link.id,
                           capability=appmod.CAP_TEAMS, method="forged"))
    db.session.commit()

    assert appmod.can(kid, appmod.CAP_TEAMS)[0] is False


def test_an_unknown_capability_is_an_error_not_a_quiet_allow(app):
    """A typo in a call site must fail loudly, not open a door."""
    kid = _user(appmod.AGE_CHILD, "synth_kid_typo")
    with pytest.raises(ValueError):
        appmod.can(kid, "team_chatt")


# ── The audit log ──────────────────────────────────────────────────────────

def test_the_audit_records_the_decision_and_never_the_content(app):
    kid = _user(appmod.AGE_CHILD, "synth_kid_audit")
    appmod.can(kid, appmod.CAP_TEAM_CHAT, record=True)
    db.session.commit()

    row = db.session.execute(db.select(PermissionAudit)).scalars().first()
    assert row.decision == "deny"
    assert row.reason == "no guardian consent"
    assert row.capability == appmod.CAP_TEAM_CHAT
    # Nothing on this model can hold a message, a photo or a conversation.
    columns = {c.name for c in PermissionAudit.__table__.columns}
    for forbidden in ("body", "content", "message", "text", "photo"):
        assert forbidden not in columns
