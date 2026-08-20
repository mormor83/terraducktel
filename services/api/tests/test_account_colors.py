"""Cloud-account colour labels: palette helpers + the API contract around them.

The point of the feature is that two accounts never look alike on the Runs page,
so most of these assert distinctness rather than specific colours.
"""
import pytest

from app.services import account_colors as ac

pytestmark = pytest.mark.usefixtures("default_bu")


def _h(token, bu="default"):
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


def _body(account_id="111111111111", **over):
    b = {
        "account_id": account_id,
        "name": f"acct-{account_id}",
        "state_bucket": f"bkt-{account_id}",
        "access_key_id": "AKIAEXAMPLE123",
        "secret_access_key": "secret",
    }
    b.update(over)
    return b


# ─── palette helpers ─────────────────────────────────────────────────────────


def test_palette_and_slack_tables_agree():
    # Slack needs a hex and an emoji for every token; a token missing from
    # either map would KeyError at notification time, not at import time.
    assert set(ac.COLOR_HEX) == set(ac.ACCOUNT_COLORS)
    assert set(ac.COLOR_EMOJI) == set(ac.ACCOUNT_COLORS)
    assert len(set(ac.COLOR_HEX.values())) == len(ac.ACCOUNT_COLORS)


def test_normalize_accepts_palette_and_clears_empty():
    assert ac.normalize("blue") == "blue"
    assert ac.normalize(" Blue ") == "blue"
    # "" and None both mean auto → NULL, not an empty-string colour.
    assert ac.normalize("") is None
    assert ac.normalize(None) is None


def test_normalize_rejects_unknown():
    # Teal in particular: `sky` is aliased to brand teal, so a teal account
    # colour would read as chrome. It must not be quietly accepted.
    for bad in ("teal", "#ff0000", "RED!"):
        with pytest.raises(ValueError):
            ac.normalize(bad)


def test_derive_is_stable_across_processes():
    # sha256, not hash() — PYTHONHASHSEED would otherwise repaint every run row
    # on each API restart.
    assert ac.derive("444444444444") == ac.derive("444444444444")
    assert ac.derive("444444444444") in ac.ACCOUNT_COLORS


def test_pick_next_avoids_taken_then_spreads():
    assert ac.pick_next([]) == ac.ACCOUNT_COLORS[0]
    assert ac.pick_next(["red"]) == "orange"
    # Every colour used once → reuse the least-used, not always the first.
    assert ac.pick_next(list(ac.ACCOUNT_COLORS) + ["red"]) == "orange"
    # Unknown/None entries in the taken list are ignored, not crashed on.
    assert ac.pick_next([None, "not-a-colour"]) == ac.ACCOUNT_COLORS[0]


def test_effective_prefers_explicit_choice():
    assert ac.effective("purple", "444444444444") == "purple"
    assert ac.effective(None, "444444444444") == ac.derive("444444444444")


# ─── API contract ────────────────────────────────────────────────────────────


async def test_created_accounts_get_distinct_colors(auth_client, admin_token):
    """The headline guarantee: no two accounts in a BU share a colour."""
    seen = []
    for i in range(4):
        r = await auth_client.post(
            "/api/v1/aws-accounts",
            json=_body(account_id=f"{i}" * 12),
            headers=_h(admin_token),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["color_effective"] == body["color"]
        seen.append(body["color"])
    assert len(set(seen)) == len(seen), seen


async def test_explicit_color_round_trips(auth_client, admin_token):
    r = await auth_client.post(
        "/api/v1/aws-accounts",
        json=_body(account_id="555555555555", color="purple"),
        headers=_h(admin_token),
    )
    assert r.status_code == 201 and r.json()["color"] == "purple"

    upd = await auth_client.put(
        f"/api/v1/aws-accounts/{r.json()['id']}",
        json={"color": "green"},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200 and upd.json()["color"] == "green"


async def test_clearing_color_falls_back_to_derived(auth_client, admin_token):
    r = await auth_client.post(
        "/api/v1/aws-accounts",
        json=_body(account_id="666666666666", color="purple"),
        headers=_h(admin_token),
    )
    upd = await auth_client.put(
        f"/api/v1/aws-accounts/{r.json()['id']}",
        json={"color": ""},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200
    # Stored NULL, but the UI still gets something to paint.
    assert upd.json()["color"] is None
    assert upd.json()["color_effective"] == ac.derive("666666666666")


async def test_invalid_color_is_rejected(auth_client, admin_token):
    r = await auth_client.post(
        "/api/v1/aws-accounts",
        json=_body(account_id="777777777777", color="teal"),
        headers=_h(admin_token),
    )
    assert r.status_code == 422


async def test_color_survives_an_unrelated_update(auth_client, admin_token):
    """A PUT that doesn't mention `color` must not clear it.

    `exclude_unset` is what makes this work; a plain model_dump would send
    color=None on every rename.
    """
    r = await auth_client.post(
        "/api/v1/aws-accounts",
        json=_body(account_id="888888888888", color="brown"),
        headers=_h(admin_token),
    )
    upd = await auth_client.put(
        f"/api/v1/aws-accounts/{r.json()['id']}",
        json={"name": "renamed"},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200 and upd.json()["color"] == "brown"
