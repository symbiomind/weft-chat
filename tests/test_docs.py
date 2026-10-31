"""
Guide pages: the files, their links, and the route.

The test that matters is ``test_every_shipped_page_is_sound``. At runtime a broken page
is logged and left out, so the service never fails to start over one; this is where the
mistake is meant to be caught instead, before it ships.

Everything here reads ``app/docs/`` or a temporary directory. Nothing touches ``data/``.
"""

import pytest

from app import docs
from tests.test_provider_routes import signed_in

GOOD = """---
title: Sessions and threads
id: guide/sessions
weight: 20
since: "1"
summary: Where a conversation is kept.
content: |
  A **session** is kept by the engine.

  - one
  - two
---

The whole page.

## A section
"""

URL = "https://docs.example.test/app/docs"


def page(path="guide/sessions.md", text=GOOD):
    return docs.parse(text, path)


# ---- the shipped files ---------------------------------------------------------


def test_every_shipped_page_is_sound():
    found, errors = docs.read_dir()
    assert errors == []
    assert found, "no guide pages shipped"


def test_the_service_pages_are_never_served():
    # They are for whoever runs weft; read on GitHub or the site, never in the app.
    built = docs.build(docs.read_dir()[0], URL)
    assert all(p["id"].startswith("guide/") for p in built["pages"])


# ---- one file ------------------------------------------------------------------


def test_a_page_parses():
    got = page()
    assert got["id"] == "guide/sessions"
    assert got["title"] == "Sessions and threads"
    assert got["weight"] == 20
    assert got["since"] == "1"
    assert got["summary"] == "Where a conversation is kept."
    assert got["content"].startswith("A **session**")
    assert got["index"] is False


@pytest.mark.parametrize("change, reason", [
    (lambda t: t.replace("---\ntitle", "title", 1), "front matter"),
    (lambda t: t.replace("id: guide/sessions\n", ""), "`id`"),
    (lambda t: t.replace("id: guide/sessions", "id: service/sessions"), "its folder"),
    (lambda t: t.replace("title: Sessions and threads\n", ""), "`title`"),
    (lambda t: t.replace("weight: 20", "weight: soon"), "`weight`"),
    (lambda t: t.split("content:")[0] + "---\n", "`content` is required"),
    (lambda t: t.replace("  - one", "  ## Steps\n\n  - one"), "heading"),
    (lambda t: t.replace("The whole page.", "# Sessions\n\nThe whole page."), "H1"),
])
def test_a_broken_page_says_why(change, reason):
    with pytest.raises(docs.DocError, match=reason):
        page(text=change(GOOD))


def test_an_index_needs_no_id_or_content():
    got = docs.parse("---\ntitle: Guide\nweight: 1\n---\n", "guide/_index.md")
    assert got["id"] == "guide"
    assert got["index"] is True
    assert got["content"] == ""


def test_a_hash_that_is_not_a_heading_is_fine():
    text = GOOD.replace("  - two", "  - two\n\n  #1 of three")
    assert "#1 of three" in page(text=text)["content"]


def test_a_bad_file_is_left_out_and_said(tmp_path, caplog):
    (tmp_path / "guide").mkdir()
    (tmp_path / "guide" / "good.md").write_text(GOOD)
    (tmp_path / "guide" / "bad.md").write_text("no front matter")
    pages, errors = docs.read_dir(tmp_path)
    assert [p["id"] for p in pages] == ["guide/sessions"]
    assert errors and errors[0].startswith("guide/bad.md")


def test_the_same_id_twice_is_a_problem(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "guide" / "a.md").write_text(GOOD)
    (tmp_path / "guide" / "b.md").write_text(GOOD)
    _, errors = docs.read_dir(tmp_path)
    assert errors == ["guide/sessions: the id is used twice"]


# ---- the set -------------------------------------------------------------------


def titled(title, weight, path):
    return page(path, GOOD.replace("Sessions and threads", title)
                .replace("weight: 20", f"weight: {weight}")
                .replace("guide/sessions", path[:-3]))


def test_pages_go_by_weight_then_title_and_unweighted_last():
    pages = [titled("Zed", 10, "guide/z.md"), titled("Bee", 0, "guide/b.md"),
             titled("Ant", 10, "guide/a.md"), titled("Cat", 5, "guide/c.md")]
    built = docs.build(pages, URL)
    assert [p["title"] for p in built["pages"]] == ["Cat", "Ant", "Zed", "Bee"]


def test_a_page_links_to_its_whole_self():
    built = docs.build([page()], URL + "/")
    assert built["pages"][0]["url"] == f"{URL}/guide/sessions.md"
    assert docs.build([page()], "")["pages"][0]["url"] is None


def test_the_index_is_the_intro_and_not_a_page():
    index = docs.parse("---\ntitle: Guide\ncontent: Start **here**.\n---\n", "guide/_index.md")
    built = docs.build([index, page()], URL)
    assert built["intro"] == "<p>Start <strong>here</strong>.</p>"
    assert [p["id"] for p in built["pages"]] == ["guide/sessions"]


def test_relative_links_open_in_the_app_or_go_out():
    other = titled("Hollow", 30, "guide/hollow.md")
    text = GOOD.replace(
        "  A **session** is kept by the engine.",
        "  See [hollow](hollow.md#key), [running](../service/proxy.md),"
        " [escape](../../secrets.md), [a tip](tip:chat/welcome) and [notes](notes.txt).")
    built = docs.build([page(text=text), other], URL)
    html = next(p for p in built["pages"] if p["id"] == "guide/sessions")["html"]
    assert '<a href="#" data-doc="guide/hollow">hollow</a>' in html
    assert f'<a href="{URL}/service/proxy.md" target="_blank"' in html
    assert '<a href="#" data-tip="chat/welcome">a tip</a>' in html
    # Outside app/docs/, or not a page: the words stay as they are.
    assert "[escape](../../secrets.md)" in html
    assert "[notes](notes.txt)" in html


def test_without_a_docs_url_an_outside_link_is_words():
    text = GOOD.replace("  A **session** is kept by the engine.", "  [running](../service/proxy.md)")
    html = docs.build([page(text=text)], "")["pages"][0]["html"]
    assert "<a" not in html


# ---- the route -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_gets_no_guide(csrf_client):
    response = await csrf_client.get("/api/guide")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_signed_in_gets_the_guide(csrf_client, session):
    await signed_in(csrf_client, session)
    response = await csrf_client.get("/api/guide")
    assert response.status_code == 200
    pages = response.json()["pages"]
    assert pages and all(p["html"] and p["title"] for p in pages)
