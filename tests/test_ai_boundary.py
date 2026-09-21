"""Only Ask Rickie may reach the model. Everything else is free, forever.

The membership model says ordinary exercise, trivia, team chat and photo
sharing never consume an AI allowance. That is currently true for a structural
reason rather than a policy one: exactly one function in the whole application
touches the Anthropic client.

Structural facts decay quietly. A future "summarise this team's week" or
"suggest a caption" would add a second call site, and the first anybody would
know is a bill or a user being charged for typing in team chat. This test is
the alarm on that door.
"""
import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app.py"

# The one function allowed to spend money at the model.
ALLOWED_MODEL_CALLERS = {"coach"}


def _functions_reaching_the_client():
    """Every top-level function whose body mentions the Anthropic client."""
    src = APP.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and inner.id == "_anthropic_lib":
                found.add(node.name)
            elif isinstance(inner, ast.Attribute) and inner.attr == "_anthropic_lib":
                found.add(node.name)
    return found


def test_only_the_coach_reaches_the_model():
    reaching = _functions_reaching_the_client()
    unexpected = reaching - ALLOWED_MODEL_CALLERS
    assert not unexpected, (
        "these functions reach the Anthropic client and are not the coach: "
        f"{sorted(unexpected)}.\n"
        "Every model call costs money and must consume a user's allowance. If "
        "this is deliberate, it needs allowance accounting FIRST, then an entry "
        "in ALLOWED_MODEL_CALLERS — not the other way round."
    )


def test_the_coach_still_does_reach_it():
    """Guards the guard: if the name is refactored this test must not pass by
    silently checking nothing."""
    assert "coach" in _functions_reaching_the_client(), (
        "coach() no longer references _anthropic_lib — either Ask Rickie is "
        "broken, or the client is reached by a new name this test cannot see")


@pytest.mark.parametrize("route_fragment", [
    "teams", "photos", "messages", "challenges", "campfire",
    "brain-boost", "daily", "moments", "memory-book",
])
def test_no_team_or_exercise_route_reaches_the_model(route_fragment):
    """Named explicitly, because 'the set is currently {coach}' is a fact about
    today and these are the routes the promise is actually about."""
    src = APP.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = " ".join(
            ast.get_source_segment(src, d) or "" for d in node.decorator_list)
        if route_fragment not in decorators or "@app.route" not in decorators:
            continue
        body = ast.get_source_segment(src, node) or ""
        assert "_anthropic_lib" not in body, (
            f"{node.name}() serves a /{route_fragment} route and reaches the "
            "model — that would charge somebody an AI allowance for "
            "exercise, trivia, team chat or photo sharing")
