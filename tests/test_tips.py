"""
Tips: the files, the small markdown, and the route.

The test that matters is ``test_every_shipped_tip_is_sound``. At runtime a broken tip is
logged and left out, so the service never fails to start over one; this is where the
mistake is meant to be caught instead, before it ships.

Everything here reads ``app/docs/tutorials/`` or a temporary directory. Nothing touches
``data/``.
"""

import re
from pathlib import Path

import pytest

from app import docs, tips
from tests.test_provider_routes import signed_in

GOOD = """---
id: providers/engine
page: providers
after: [drawer/start]
done_on: providers-changed
since: 2
other_stuffs_we_can_use: "stuffs"
---
# Add an engine

Tap **+ Add**.
"""


# ---- the shipped files ---------------------------------------------------------


def test_every_shipped_tip_is_sound():
    found, errors = tips.read_dir(tips.TIPS_DIR)
    assert errors == []
    assert found, "no tips shipped"


def test_the_first_tip_needs_nothing_before_it():
    found, _ = tips.read_dir(tips.TIPS_DIR)
    starts = [t for t in found if t["group"] == "core" and not t["after"]]
    assert [t["id"] for t in starts] == ["chat/welcome"]


def test_no_core_tip_waits_on_a_hidden_one():
    # The game rides on top of the tutorial; nothing anyone needs sits behind it.
    found, _ = tips.read_dir(tips.TIPS_DIR)
    secret = {t["id"] for t in found if t["group"] == "secret"}
    for tip in found:
        if tip["group"] != "secret":
            assert not set(tip["after"]) & secret, tip["id"]


# ---- the tips, the guide and the screens together ----------------------------

#: Visible tips that have no guide page yet, so with tips off their (?) still opens the
#: tip. This list only shrinks: a new tip without a page fails, and so does a tip on it
#: that has gained one. It is the docs to-do list.
NO_GUIDE_YET = {
    "chat/welcome", "chat/pick", "drawer/start", "wefts/first",
    "providers/engine", "providers/speech", "providers/sessions",
    "weft/engine",
}

#: Where the screens are built. A heading is a section by `"data-section": "page/name"`.
VIEWS = Path(__file__).parent.parent / "web" / "js"
_SECTION_IN_VIEW = re.compile(r'"data-section": "([^"]+)"')


def _sections_in_views() -> list[str]:
    return [name for path in sorted(VIEWS.rglob("*.js"))
            for name in _SECTION_IN_VIEW.findall(path.read_text(encoding="utf-8"))]


def test_every_visible_tip_has_a_guide_page_or_is_on_the_list():
    found, _ = tips.read_dir(tips.TIPS_DIR)
    visible = {t["id"]: t for t in found if t["group"] != "secret"}
    pages = {p["id"] for p in docs.read_dir()[0] if not p["index"]}
    for tip in visible.values():
        if tip["guide"]:
            assert tip["guide"] in pages, f"{tip['id']}: `guide` names {tip['guide']}, not a page"
    missing = {i for i, t in visible.items() if not t["guide"]}
    assert not missing - NO_GUIDE_YET, "a new tip needs `guide:` (or a page written first)"
    assert not NO_GUIDE_YET - missing, "these have a page now: take them off NO_GUIDE_YET"


def test_every_section_heading_is_named_once_with_its_page():
    names = _sections_in_views()
    assert names, "no sections found: has the view code moved?"
    assert len(names) == len(set(names)), "a section name is used twice"
    assert all(tips.SECTION.match(n) for n in names), "name sections page first: weft/door"


def test_every_claimed_section_exists_and_is_claimed_once():
    found, _ = tips.read_dir(tips.TIPS_DIR)
    claims = [t["section"] for t in found if t["section"]]
    claims += [s for p in docs.read_dir()[0] for s in p["sections"]]
    assert len(claims) == len(set(claims)), "a section is claimed twice"
    assert not set(claims) - set(_sections_in_views()), "a claimed section is on no screen"


# ---- one file ------------------------------------------------------------------


def test_a_tip_parses():
    tip = tips.parse(GOOD)
    assert tip["id"] == "providers/engine"
    assert tip["group"] == "core"
    assert tip["after"] == ["drawer/start"]
    assert tip["since"] == "2"
    assert tip["title"] == "Add an engine"
    assert tip["html"] == "<p>Tap <strong>+ Add</strong>.</p>"


@pytest.mark.parametrize("change, reason", [
    (("id: providers/engine\n", ""), "`id` is required"),
    (("id: providers/engine", "id: Providers Engine"), "`id` is required"),
    (("page: providers", "page: kitchen"), "`page`"),
    (("after: [drawer/start]", "after: drawer/start"), "`after`"),
    (("done_on: providers-changed", "done_on: someday"), "`done_on`"),
    (("# Add an engine", "Add an engine"), "# Title"),
    (("page: providers", "page: providers\nsection: engines"), "`section`"),
    (("page: providers", "page: providers\nguide: engines"), "`guide`"),
    (("---\nid", "id"), "front matter"),
])
def test_a_broken_tip_says_why(change, reason):
    with pytest.raises(tips.TipError, match=reason):
        tips.parse(GOOD.replace(*change, 1))


def test_problems_across_tips():
    a = {"id": "a", "after": ["b"]}
    b = {"id": "b", "after": ["a"]}
    c = {"id": "c", "after": ["nowhere"]}
    found = tips.problems([a, b, c, dict(c)])
    assert "c: the id is used twice" in found
    assert any("which is not a tip" in p for p in found)
    assert any("loop" in p for p in found)


def test_a_bad_file_is_left_out_with_whatever_waits_on_it(tmp_path, caplog):
    (tmp_path / "one.md").write_text(GOOD.replace("after: [drawer/start]\n", "").replace(
        "id: providers/engine", "id: one"))
    (tmp_path / "two.md").write_text(GOOD.replace("drawer/start", "one").replace(
        "id: providers/engine", "id: two").replace("page: providers", "page: kitchen"))
    (tmp_path / "three.md").write_text(GOOD.replace("drawer/start", "two").replace(
        "id: providers/engine", "id: three"))

    served = tips.load(tmp_path)

    assert [t["id"] for t in served] == ["one"]
    assert "two.md" in caplog.text


# ---- markdown ------------------------------------------------------------------


def test_markdown_paragraphs_and_lists():
    out = tips.render_markdown("One\nline.\n\n1. first\n2. second\n\n- a\n- b")
    assert out == "<p>One line.</p><ol><li>first</li><li>second</li></ol><ul><li>a</li><li>b</li></ul>"


def test_markdown_marks_and_links():
    out = tips.render_markdown(
        "**bold** *soft* `a **b**` [next](tip:wefts/first) [site](https://example.com)")
    assert "<strong>bold</strong>" in out
    assert "<em>soft</em>" in out
    assert "<code>a **b**</code>" in out
    assert '<a href="#" data-tip="wefts/first">next</a>' in out
    assert 'href="https://example.com" target="_blank" rel="noopener noreferrer"' in out


def test_markdown_escapes_and_refuses_other_links():
    out = tips.render_markdown('<img src=x onerror=alert(1)> [x](javascript:alert(1))')
    assert "<img" not in out
    assert "&lt;img" in out
    assert "<a" not in out


# ---- the route -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_gets_no_tips(csrf_client):
    response = await csrf_client.get("/api/tips")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_signed_in_gets_every_tip(csrf_client, session):
    await signed_in(csrf_client, session)
    response = await csrf_client.get("/api/tips")
    assert response.status_code == 200
    ids = [t["id"] for t in response.json()["tips"]]
    assert "chat/welcome" in ids
    # Hidden ones too: the client decides what is unlocked.
    assert "drawer/name" in ids


# ---- progress, kept with the account ----------------------------


def test_a_hint_is_for_hidden_tips_and_short():
    secret = GOOD.replace("page: providers", "page: drawer\ngroup: secret")
    assert tips.parse(secret.replace("since: 2", "since: 2\nhint: look closer"))["hint"] == "look closer"
    with pytest.raises(tips.TipError):
        tips.parse(GOOD.replace("since: 2", "since: 2\nhint: look closer"))
    with pytest.raises(tips.TipError):
        tips.parse(secret.replace("since: 2", "since: 2\nhint: " + "x" * 61))


@pytest.mark.asyncio
async def test_a_new_account_has_tips_on_and_nothing_done(csrf_client, session):
    await signed_in(csrf_client, session)
    body = (await csrf_client.get("/api/tips")).json()
    assert body["on"] is True
    assert body["done"] == []


@pytest.mark.asyncio
async def test_finishing_is_kept_against_the_tips_since(csrf_client, session):
    await signed_in(csrf_client, session)
    shipped = {t["id"]: t for t in tips.load()}

    response = await csrf_client.post(
        "/api/tips/done", json={"ids": ["chat/welcome", "chat/welcome", "no/such-tip"]})
    assert response.status_code == 200
    assert response.json()["added"] == ["chat/welcome"]
    # Twice is fine, and changes nothing.
    again = await csrf_client.post("/api/tips/done", json={"ids": ["chat/welcome"]})
    assert again.json()["added"] == []

    done = (await csrf_client.get("/api/tips")).json()["done"]
    assert [(d["id"], d["since"]) for d in done] == [
        ("chat/welcome", shipped["chat/welcome"]["since"])]


@pytest.mark.asyncio
async def test_progress_is_per_account(csrf_client, session):
    await signed_in(csrf_client, session)
    await csrf_client.post("/api/tips/done", json={"ids": ["chat/welcome"]})
    await csrf_client.put("/api/tips/on", json={"on": False})

    await signed_in(csrf_client, session, username="bob")
    body = (await csrf_client.get("/api/tips")).json()
    assert body["done"] == []
    assert body["on"] is True


@pytest.mark.asyncio
async def test_the_switch_is_kept_with_the_account(csrf_client, session):
    await signed_in(csrf_client, session)
    assert (await csrf_client.put("/api/tips/on", json={"on": False})).status_code == 200
    assert (await csrf_client.get("/api/tips")).json()["on"] is False


@pytest.mark.asyncio
async def test_restarting_the_help_keeps_what_was_found(csrf_client, session):
    user = await signed_in(csrf_client, session)
    await csrf_client.post("/api/tips/done", json={"ids": ["chat/welcome", "drawer/name"]})
    await csrf_client.put("/api/tips/on", json={"on": False})

    body = (await csrf_client.post("/api/tips/restart", json={"what": "help"})).json()
    assert [d["id"] for d in body["done"]] == ["drawer/name"]
    assert body["on"] is True
    assert await tips.has_done(session, user.id, "drawer/name")

    body = (await csrf_client.post("/api/tips/restart", json={"what": "found"})).json()
    assert body["done"] == []
    assert not await tips.has_done(session, user.id, "drawer/name")


@pytest.mark.asyncio
async def test_progress_needs_a_signed_in_user(csrf_client):
    assert (await csrf_client.post("/api/tips/done", json={"ids": ["chat/welcome"]})).status_code == 401
    assert (await csrf_client.put("/api/tips/on", json={"on": False})).status_code == 401
