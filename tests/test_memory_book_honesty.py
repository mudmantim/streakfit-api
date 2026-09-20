"""Rickie may not claim to have noticed a pattern that is not there.

The Memory Book told a user who had done each of five moves exactly once:

    "Your favorite move seems to be Wall Sit. Rickie's noticed."
    "You gravitate toward Mobility days."

An independent reviewer called that the moment the companion stopped reading as
honest, and they were right — it is worse than a wrong number, because it is a
claim about *them*. The query took the top row of a GROUP BY with no minimum
and no margin, so after one mission every exercise was tied at one completion
and it returned whichever row sorted first.

There is a second, quieter problem the threshold does not fix and the copy
does: the app CHOOSES the five daily moves. The user never expressed a
preference, so "favourite" was never a claim this data could support at any
sample size. The client now says "done most often", which is a fact.
"""
import datetime as dt

from app import DailyCompletion, User, db
from conftest import auth_headers, register_and_login


def _mk(client, name):
    token = register_and_login(client, name, "TestPass123!")
    uid = db.session.execute(
        db.select(User.id).where(User.username == name)).scalar_one()
    return token, uid


def _complete(uid, key, times, start_days_ago=40):
    """Spread completions across distinct days — one row per (user, date, key)."""
    for i in range(times):
        db.session.add(DailyCompletion(
            user_id=uid, exercise_key=key,
            date=dt.date.today() - dt.timedelta(days=start_days_ago - i)))
    db.session.commit()


def _favourites(client, token):
    body = client.get("/api/memory-book", headers=auth_headers(token)).get_json()
    return body["favorites"]


def test_one_mission_produces_no_favourite_at_all(client, app):
    """Five moves done once each is a tie, not a preference."""
    token, uid = _mk(client, "mb_one_mission")
    for key in ("knee_push_up", "bodyweight_squat", "superman",
                "childs_pose", "jumping_jack"):
        _complete(uid, key, 1)

    fav = _favourites(client, token)
    assert fav["favorite_exercise"] is None, (
        f"claimed a favourite move from a five-way tie: {fav['favorite_exercise']}")
    assert fav["favorite_category"] is None, (
        "claimed a favourite category from one mission")


def test_a_narrow_lead_is_not_a_favourite(client, app):
    """Four vs three is the daily rotation talking, not the person."""
    token, uid = _mk(client, "mb_narrow")
    _complete(uid, "bodyweight_squat", 4)
    _complete(uid, "superman", 3, start_days_ago=20)

    assert _favourites(client, token)["favorite_exercise"] is None, \
        "a one-completion lead was reported as a favourite"


def test_a_real_lead_is_reported(client, app):
    """The feature still works when there IS something to report."""
    token, uid = _mk(client, "mb_real")
    _complete(uid, "bodyweight_squat", 9)
    _complete(uid, "superman", 2, start_days_ago=20)

    fav = _favourites(client, token)
    assert fav["favorite_exercise"] is not None, \
        "nine completions against two reported no most-done move"
    assert fav["favorite_category"] is not None


def test_enough_completions_but_no_margin_is_still_nothing(client, app):
    """Volume alone is not a signal — two moves at eight each is a tie."""
    token, uid = _mk(client, "mb_tied_high")
    _complete(uid, "bodyweight_squat", 8)
    _complete(uid, "superman", 8, start_days_ago=20)

    assert _favourites(client, token)["favorite_exercise"] is None, \
        "a dead heat at eight completions each was reported as a favourite"


def test_a_brand_new_account_claims_nothing(client, app):
    token, _uid = _mk(client, "mb_brand_new")
    fav = _favourites(client, token)
    assert fav["favorite_exercise"] is None
    assert fav["favorite_category"] is None
