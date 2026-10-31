"""
Guide pages: the help that needs more than a tip, read in the app's Guide screen.

The files live under ``app/docs/``, written to be read in three places: on GitHub as
they are, through a site generator (Hugo), and here. Only ``guide/`` is served in the
app; ``service/`` is for whoever runs weft, and reverse proxies don't help someone on a
phone. ``tutorials/`` is the tips (``app/tips.py``), with rules of their own.

One page::

    ---
    title: A weft with no engine     # required: the list, the page's header, Hugo's H1
    id: guide/hollow-weft            # required, stable, starts with its folder
    weight: 40                       # order in the list, lower first (Hugo's own field)
    since: "1"                       # the version it describes
    summary: One line for the list row.
    content: |
      The short version, read on a phone. This is all the app shows of the page, so it
      keeps to the small markdown tips use: no headings.
    ---

    The full page, for GitHub and the site. Headings start at ##: the title is the H1.

**The app reads ``content:``, never the body.** The body is linked as "the whole page",
at ``server.docs.url`` (``config.docs_url``). A ``_index.md`` is the folder's own page:
its ``content:``, if it has one, is the intro at the top of the Guide screen.

**Links are relative paths to the ``.md`` file**, so they work on GitHub and in Hugo as
written. Here, one to another guide page opens it in the app (``doc:<id>``), and one to
anything else under ``app/docs/`` goes out to the docs url. ``doc:`` and ``tip:`` work
too, for the app only.

**A bad page is skipped, not fatal**, as with tips: ``load`` logs and leaves it out, and
``tests/test_docs.py`` runs the strict check over every shipped file.
"""

import logging
import posixpath
import re
from pathlib import Path

import yaml

from app import config
from app.markdown import render_markdown

logger = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).parent / "docs"

#: The one folder the app serves.
SERVED = "guide"

_ID = re.compile(r"^[a-z0-9][a-z0-9/-]*$")
_FRONT = re.compile(r"\A---\n(.*?)\n---[ \t]*(?:\n(.*))?\Z", re.S)
_HEADING = re.compile(r"^\s*#{1,6}\s", re.M)


class DocError(ValueError):
    """A page that cannot be used, with the reason."""


# ---------------------------------------------------------------------------
# One file
# ---------------------------------------------------------------------------

def _optional_str(meta: dict, key: str) -> str | None:
    value = meta.get(key)
    if value is None:
        return None
    if not isinstance(value, (str, int, float)):
        raise DocError(f"`{key}` must be a single value")
    return str(value)


def parse(text: str, path: str) -> dict:
    """One page, by its path under ``app/docs/`` (``guide/x.md``). Raises DocError."""
    match = _FRONT.match(text.replace("\r\n", "\n"))
    if not match:
        raise DocError("no front matter: the file must start with a --- block")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise DocError(f"front matter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise DocError("front matter must be a set of key: value lines")

    folder = path.split("/")[0]
    is_index = posixpath.basename(path) == "_index.md"

    doc_id = _optional_str(meta, "id") or (folder if is_index else None)
    if not doc_id or not _ID.match(doc_id):
        raise DocError("`id` is required: lower case, digits, / and -")
    if not is_index and not doc_id.startswith(f"{folder}/"):
        raise DocError(f"`id` must start with its folder, `{folder}/`")
    title = _optional_str(meta, "title")
    if not title:
        raise DocError("`title` is required")
    weight = meta.get("weight", 0)
    if not isinstance(weight, int) or isinstance(weight, bool):
        raise DocError("`weight` must be a whole number")

    content = meta.get("content")
    if content is not None and not isinstance(content, str):
        raise DocError("`content` must be text: use `content: |` and indent it")
    if not content and not is_index:
        raise DocError("`content` is required: it is what the app shows")
    if content and _HEADING.search(content):
        raise DocError("`content` has a heading: keep it to paragraphs and lists")

    body = (match.group(2) or "").lstrip()
    if body.startswith("# "):
        raise DocError("the body starts with a # heading: the title is the H1, use ##")

    return {
        "id": doc_id,
        "path": path,
        "index": is_index,
        "title": title,
        "weight": weight,
        "since": _optional_str(meta, "since") or "",
        "summary": _optional_str(meta, "summary"),
        "content": content or "",
    }


# ---------------------------------------------------------------------------
# The set
# ---------------------------------------------------------------------------

def read_dir(directory: Path = DOCS_DIR, folder: str = SERVED) -> tuple[list[dict], list[str]]:
    """Every page in the folder, and what was wrong with the ones left out."""
    pages, errors = [], []
    for path in sorted((directory / folder).glob("*.md")):
        name = f"{folder}/{path.name}"
        try:
            pages.append(parse(path.read_text(encoding="utf-8"), name))
        except (DocError, OSError, UnicodeDecodeError) as exc:
            errors.append(f"{name}: {exc}")
    ids = [p["id"] for p in pages]
    for doc_id in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"{doc_id}: the id is used twice")
    return pages, errors


def _resolver(page: dict, by_path: dict[str, str], docs_url: str):
    """How a relative link on this page is followed: in the app, out, or not at all."""
    here = posixpath.dirname(page["path"])

    def resolve(target: str) -> str | None:
        file, _, _anchor = target.partition("#")
        if not file.endswith(".md"):
            return None
        path = posixpath.normpath(posixpath.join(here, file))
        if path.startswith("../") or path.startswith("/"):
            return None
        if path in by_path:
            return f"doc:{by_path[path]}"
        return f"{docs_url}/{path}" if docs_url else None

    return resolve


def build(pages: list[dict], docs_url: str) -> dict:
    """What the Guide screen gets: the intro, and the pages in order, rendered."""
    docs_url = docs_url.rstrip("/")
    by_path = {p["path"]: p["id"] for p in pages if not p["index"]}
    intro = None
    out = []
    # Hugo's order: by weight, an unset (0) weight last, then by title.
    for page in sorted(pages, key=lambda p: (p["weight"] == 0, p["weight"], p["title"].lower())):
        html = render_markdown(page["content"], _resolver(page, by_path, docs_url))
        if page["index"]:
            intro = html or None
            continue
        out.append({
            "id": page["id"],
            "title": page["title"],
            "summary": page["summary"],
            "since": page["since"],
            "html": html,
            "url": f"{docs_url}/{page['path']}" if docs_url else None,
        })
    return {"intro": intro, "pages": out}


_cache: tuple[tuple, dict] | None = None


def load(directory: Path = DOCS_DIR) -> dict:
    """The Guide screen's pages, re-read whenever a file changes, as tips are."""
    global _cache
    docs_url = config.docs_url()
    try:
        paths = sorted((directory / SERVED).glob("*.md"))
        key = (str(directory), docs_url) + tuple((p.name, p.stat().st_mtime_ns) for p in paths)
    except OSError:
        return {"intro": None, "pages": []}
    if _cache and _cache[0] == key:
        return _cache[1]

    pages, errors = read_dir(directory)
    for error in errors:
        logger.error("Guide page left out: %s", error)
    seen: set[str] = set()
    pages = [p for p in pages if not (p["id"] in seen or seen.add(p["id"]))]
    built = build(pages, docs_url)
    _cache = (key, built)
    return built
