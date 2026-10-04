#!/usr/bin/env python3
"""Ask Rickie, the 1:1 coach chat (POST /api/coach) -- its boundaries (D48/D53).

Production-safe by construction: every request here is one the route must
REFUSE before it reaches the model, so the suite never spends money on an AI
call and never depends on the provider being up. That is also the point --
these are the refusals that keep client text out of the system prompt and
bad input away from the provider:

- the route requires a login;
- a body that is not a JSON object is a stable 400, not a 500;
- an insight longer than any real one is refused (it used to be pasted,
  uncapped, into Rickie's SYSTEM prompt);
- text that cannot be stored (a NUL) is refused up front instead of getting
  a reply that is silently never saved.

The coach is limited to 3 questions a minute per person and every refusal
counts, so this module makes exactly three authenticated calls. The fourth
check reads the self-verification payload: the provider deadline that stops a
stalled model from freezing the one worker is reported there.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import build_team_scenario


def run(api, results, scenario):
    token = scenario.users["a"]["token"]

    status, _ = api.request("POST", "/api/coach", body={"message": "hi"})
    results.check("coach.requires_login", status == 401, f"status={status}")

    status, payload = api.request("POST", "/api/coach", token=token, body=["not", "an", "object"])
    results.check("coach.wrong_shape_is_a_400",
                  status == 400 and (payload or {}).get("error") == "invalid_request",
                  f"status={status} payload={payload}")

    status, payload = api.request("POST", "/api/coach", token=token, body={
        "message": "tell me more",
        "context": {"type": "insight", "insight_text": "a" * 5000}})
    results.check("coach.oversized_insight_is_refused",
                  status == 400 and (payload or {}).get("error") == "context_too_long",
                  f"status={status} payload={payload}")

    status, payload = api.request("POST", "/api/coach", token=token,
                                  body={"message": "hi\u0000there"})
    results.check("coach.unstorable_text_is_refused",
                  status == 400 and (payload or {}).get("error") == "invalid_message",
                  f"status={status} payload={payload}")

    status, payload = api.request("GET", "/api/verification/self")
    by_id = {c.get("id"): c for c in (payload or {}).get("checks", [])}
    bounds = by_id.get("coach.provider_bounds") or {}
    results.check("coach.provider_deadline_in_force",
                  bounds.get("status") == "PASS" and "0 retries" in (bounds.get("observed") or ""),
                  f"status={bounds.get('status')!r} observed={bounds.get('observed')!r}")

    return scenario


if __name__ == "__main__":
    run_module_standalone("Ask Rickie coach verification", build_team_scenario, run)
