#!/usr/bin/env python3
"""Team photos subsystem — private sharing, filter unlocks, and who can read bytes.

This module is why the suite is allowed near production: it only ever uploads a
few hundred bytes of generated JPEG into a throwaway `Smoke Test` team, and
deletes what it uploads on the way out.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import build_team_scenario

# A minimal but genuinely decodable 64x64 JPEG, built inline so the suite needs
# no binary fixture and no image library (standard library only, by contract).
_SOF0 = bytes([0xFF, 0xC0, 0x00, 0x11, 0x08, 0x00, 0x40, 0x00, 0x40, 0x03,
               0x01, 0x11, 0x00, 0x02, 0x11, 0x01, 0x03, 0x11, 0x01])
_SOS = bytes([0xFF, 0xDA, 0x00, 0x0C, 0x03, 0x01, 0x00, 0x02, 0x11, 0x03,
              0x11, 0x00, 0x3F, 0x00])
SMOKE_JPEG = b"\xff\xd8" + _SOF0 + _SOS + b"\x9a\x4b\x11\x22" + b"\xff\xd9"


def _multipart(fields, filename, blob):
    boundary = "----streakfitsmoke" + uuid.uuid4().hex
    body = b""
    for key, value in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="{key}"\r\n\r\n{value}\r\n').encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
             f'filename="{filename}"\r\nContent-Type: image/jpeg\r\n\r\n').encode()
    body += blob + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def run(api, results, scenario):
    users = scenario.users
    team_id = scenario.team_id
    owner, member, outsider = users["a"], users["b"], users["outsider"]

    # --- The filter catalog is reachable and reports lock state -------------
    status, catalog = api.request("GET", "/api/photo-filters", token=member["token"])
    ok = results.check("photos.filter_catalog_readable",
                       status == 200 and isinstance(catalog.get("filters"), list),
                       f"status={status}")
    if ok:
        by_key = {f["key"]: f for f in catalog["filters"]}
        results.check("photos.free_filter_is_unlocked",
                      by_key.get("rickie_peek", {}).get("unlocked") is True)
        results.check("photos.earned_filter_is_locked_for_a_new_user",
                      by_key.get("rickie_proud", {}).get("unlocked") is False)
        results.check("photos.acorn_filter_advertises_its_price",
                      (by_key.get("golden_hour", {}).get("cost") or 0) > 0)
        results.check("photos.catalog_reports_a_spendable_balance",
                      isinstance(catalog.get("acorns_available"), int))

    # --- Upload -------------------------------------------------------------
    body, content_type = _multipart(
        {"caption": "smoke test photo", "filter_key": "rickie_peek"}, "smoke.jpg", SMOKE_JPEG)
    status, created = api.request("POST", f"/api/teams/{team_id}/photos",
                                  token=member["token"], raw_body=body,
                                  content_type=content_type)
    ok = results.check("photos.upload_accepted", status == 201, f"status={status}")
    if not ok:
        return scenario

    photo = (created or {}).get("photo") or {}
    public_id = photo.get("public_id")
    results.check("photos.upload_returns_an_opaque_id",
                  isinstance(public_id, str) and len(public_id) == 32,
                  f"public_id={public_id!r}")
    results.check("photos.caption_is_kept", photo.get("caption") == "smoke test photo")

    photo_path = f"/api/teams/{team_id}/photos/{public_id}"

    # --- Who can read the bytes --------------------------------------------
    status, _ = api.request("GET", photo_path, token=owner["token"])
    results.check("photos.teammate_can_view", status == 200, f"status={status}")

    status, _ = api.request("GET", photo_path, token=outsider["token"])
    results.check("photos.non_member_cannot_view", status == 403, f"status={status}")

    status, _ = api.request("GET", photo_path)
    results.check("photos.anonymous_cannot_view", status == 401, f"status={status}")

    body2, ct2 = _multipart({}, "smoke.jpg", SMOKE_JPEG)
    status, _ = api.request("POST", f"/api/teams/{team_id}/photos",
                            token=outsider["token"], raw_body=body2, content_type=ct2)
    results.check("photos.non_member_cannot_upload", status == 403, f"status={status}")

    # --- What the server refuses -------------------------------------------
    body3, ct3 = _multipart({}, "not.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    status, _ = api.request("POST", f"/api/teams/{team_id}/photos",
                            token=member["token"], raw_body=body3, content_type=ct3)
    results.check("photos.non_jpeg_rejected", status == 400, f"status={status}")

    body4, ct4 = _multipart({"filter_key": "rickie_proud"}, "smoke.jpg", SMOKE_JPEG)
    status, _ = api.request("POST", f"/api/teams/{team_id}/photos",
                            token=member["token"], raw_body=body4, content_type=ct4)
    results.check("photos.locked_filter_refused_server_side", status == 403, f"status={status}")

    # --- It lands in the thread and in the team's history -------------------
    status, thread = api.request("GET", f"/api/teams/{team_id}/messages", token=owner["token"])
    ok = results.check("photos.thread_readable", status == 200, f"status={status}")
    if ok:
        entries = [m for m in thread if m.get("photo")]
        results.check("photos.appears_in_the_team_thread", len(entries) >= 1,
                      f"{len(entries)} photo entries")
        if entries:
            results.check("photos.thread_carries_no_image_bytes",
                          "image_data" not in entries[0]["photo"])

    status, moments = api.request("GET", f"/api/teams/{team_id}/moments", token=owner["token"])
    if status == 200:
        results.check("photos.recorded_in_team_history",
                      any(m["moment_type"] == "photo_shared" for m in moments))

    # --- Deletion, which is also this module's own cleanup ------------------
    status, _ = api.request("DELETE", photo_path, token=member["token"])
    results.check("photos.sender_can_delete", status == 200, f"status={status}")

    status, _ = api.request("GET", photo_path, token=owner["token"])
    results.check("photos.deleted_bytes_stop_being_served", status == 404, f"status={status}")

    return scenario


if __name__ == "__main__":
    run_module_standalone("Team photos verification", build_team_scenario, run)
