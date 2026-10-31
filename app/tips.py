"""
Tips: short help that shows itself as the user finds their way around.

Each tip is one markdown file under ``app/docs/tutorials/``, with YAML front matter that
says where it shows and what has to come first::

    ---
    id: providers/engine        # stable: progress is kept against this, not the filename
    page: providers             # where it shows by itself
    group: core                 # core | advanced | secret
    after: [drawer/start]       # tips that must be done (or not apply) first
    done_on: providers-changed  # doing this finishes it, as well as Got it
    ---
    # Add an engine

    The body, in a small markdown: paragraphs, lists, **bold**, *italic*, `code`, and
    links, including [another tip](tip:wefts/first).

**The account keeps the progress, not the browser**. Which tips someone
has finished is a row per tip in ``tip_marks``, so a new phone or a cleared browser
loses nothing, and the server can say whether an account has found a hidden tip when
finding one unlocks something (``has_done``). The rule for which tip shows next is
still the client's (``web/js/tips.js``); the server serves every tip to every signed-in
user, the hidden ones included. A hidden tip is an easter egg, not a secret: its text
never says it is hidden or how it is found.

**Why the files live in ``app/`` and not ``web/``.** Everything under ``web/`` is served
as a plain file to anyone, signed in or not. These are read here and served through an
authenticated route.

**A bad tip is skipped, not fatal.** A restart also migrates the database, and a typo in a
tip must never be what stops the service coming up. So ``load`` logs and leaves it out,
and ``tests/test_tips.py`` runs the strict check over every shipped file, which is where a
mistake should be caught.

**The markdown is rendered here, once, and deliberately small** (``app/markdown.py``,
shared with the guide pages in ``app/docs.py``). The HTML arrives ready, and a tip can
link to a guide page as well as to another tip.
"""

import logging
import re
from pathlib import Path

import yaml
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.markdown import render_markdown
from app.models import TipMark

logger = logging.getLogger(__name__)

TIPS_DIR = Path(__file__).parent / "docs" / "tutorials"

#: Where a tip can show. The client names the same screens when it reports a visit.
PAGES = {"chat", "drawer", "providers", "wefts", "weft", "account", "about", "notifications"}

GROUPS = {"core", "advanced", "secret"}

#: What the client reports, besides `open:<page>` for every visit. A tip naming anything
#: else would wait forever for an event that never comes.
EVENTS = {f"open:{page}" for page in PAGES} | {
    "providers-changed", "wefts-changed", "shelf-open", "wordmark-x3", "theme-x4",
    # Never sent: the wordmark finds ELIZA's tip directly (tutor.find). Named so her
    # tip waits for it, rather than showing by itself when the drawer opens.
    "eliza-found",
    # Never sent either: the status light finds its tip directly, and that tip's link
    # finds the next.
    "status-found",
}

#: When a tip applies at all. A tip that does not apply counts as done for anything
#: waiting on it, which is how an account that is already set up skips the setup tips.
#: `core-done` waits for every core tip, wherever it lives, without naming them all.
CONDITIONS = {"no-wefts", "core-done"}

#: Longest `hint:`, the line a hidden tip shows on the About page before it is found.
HINT_MAX = 60

_ID = re.compile(r"^[a-z0-9][a-z0-9/-]*$")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)


class TipError(ValueError):
    """A tip file that cannot be used, with the reason."""


# ---------------------------------------------------------------------------
# One file
# ---------------------------------------------------------------------------

def _optional_str(meta: dict, key: str) -> str | None:
    value = meta.get(key)
    if value is None:
        return None
    if not isinstance(value, (str, int, float)):
        raise TipError(f"`{key}` must be a single value")
    return str(value)


def parse(text: str) -> dict:
    """One tip file into the shape the client gets. Raises TipError with the reason."""
    match = _FRONT.match(text.replace("\r\n", "\n"))
    if not match:
        raise TipError("no front matter: the file must start with a --- block")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise TipError(f"front matter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise TipError("front matter must be a set of key: value lines")

    tip_id = _optional_str(meta, "id")
    if not tip_id or not _ID.match(tip_id):
        raise TipError("`id` is required: lower case, digits, / and -")
    page = meta.get("page")
    if page not in PAGES:
        raise TipError(f"`page` must be one of {sorted(PAGES)}")
    group = meta.get("group", "core")
    if group not in GROUPS:
        raise TipError(f"`group` must be one of {sorted(GROUPS)}")
    after = meta.get("after") or []
    if not isinstance(after, list) or not all(isinstance(a, str) for a in after):
        raise TipError("`after` must be a list of tip ids")
    order = meta.get("order", 9)
    if not isinstance(order, int):
        raise TipError("`order` must be a whole number")
    for key in ("done_on", "show_on"):
        if meta.get(key) is not None and meta[key] not in EVENTS:
            raise TipError(f"`{key}` must be one of {sorted(EVENTS)}")
    when = meta.get("when")
    if when is not None and when not in CONDITIONS:
        raise TipError(f"`when` must be one of {sorted(CONDITIONS)}")

    hint = _optional_str(meta, "hint")
    if hint is not None and (group != "secret" or len(hint) > HINT_MAX):
        raise TipError(f"`hint` is for hidden tips only, at most {HINT_MAX} characters")

    body = match.group(2).strip()
    heading, _, rest = body.partition("\n")
    if not heading.startswith("# "):
        raise TipError("the body must start with a `# Title` line")

    return {
        "id": tip_id,
        "page": page,
        "group": group,
        "order": order,
        "after": after,
        "section": _optional_str(meta, "section"),
        "point_at": _optional_str(meta, "point_at"),
        "done_on": meta.get("done_on"),
        "show_on": meta.get("show_on"),
        "when": when,
        "since": _optional_str(meta, "since") or "",
        "hint": hint,
        "title": heading[2:].strip(),
        "html": render_markdown(rest),
    }


# ---------------------------------------------------------------------------
# The set
# ---------------------------------------------------------------------------

def problems(tips: list[dict]) -> list[str]:
    """What is wrong across tips: a repeated id, an `after` naming nothing, a loop."""
    found = []
    ids = [t["id"] for t in tips]
    for tip_id in sorted({i for i in ids if ids.count(i) > 1}):
        found.append(f"{tip_id}: the id is used twice")
    known = set(ids)
    for tip in tips:
        for needed in tip["after"]:
            if needed not in known:
                found.append(f"{tip['id']}: `after` names {needed}, which is not a tip")

    # A loop in `after` is a set of tips that can never show.
    by_id = {t["id"]: t for t in tips}
    state: dict[str, str] = {}

    def visit(tip_id: str, path: list[str]) -> None:
        if state.get(tip_id) == "done" or tip_id not in by_id:
            return
        if state.get(tip_id) == "open":
            found.append("`after` goes round in a loop: " + " -> ".join(path + [tip_id]))
            return
        state[tip_id] = "open"
        for needed in by_id[tip_id]["after"]:
            visit(needed, path + [tip_id])
        state[tip_id] = "done"

    for tip_id in by_id:
        visit(tip_id, [])
    return found


def read_dir(directory: Path) -> tuple[list[dict], list[str]]:
    """Every tip in the directory, and what was wrong with the ones left out."""
    tips, errors = [], []
    for path in sorted(directory.glob("*.md")):
        try:
            tips.append(parse(path.read_text(encoding="utf-8")))
        except (TipError, OSError, UnicodeDecodeError) as exc:
            errors.append(f"{path.name}: {exc}")
    errors.extend(problems(tips))
    return tips, errors


_cache: tuple[tuple, list[dict]] | None = None


def load(directory: Path = TIPS_DIR) -> list[dict]:
    """The tips to serve, re-read whenever a file changes.

    Keyed on the files' names and modification times, so editing a tip shows on the
    next request without a restart, and an unchanged directory costs one listing.
    """
    global _cache
    try:
        paths = sorted(directory.glob("*.md"))
        key = (str(directory),) + tuple((p.name, p.stat().st_mtime_ns) for p in paths)
    except OSError:
        return []
    if _cache and _cache[0] == key:
        return _cache[1]

    tips, errors = read_dir(directory)
    for error in errors:
        logger.error("Tip left out: %s", error)
    if errors:
        # Drop anything whose `after` names a tip that is not being served, and keep
        # going until nothing changes: one bad file must not strand the rest silently.
        served = {t["id"] for t in tips}
        while True:
            kept = [t for t in tips if all(a in served for a in t["after"])]
            if len(kept) == len(tips):
                break
            tips, served = kept, {t["id"] for t in kept}
        seen: set[str] = set()
        tips = [t for t in tips if not (t["id"] in seen or seen.add(t["id"]))]
    _cache = (key, tips)
    return tips


# ---------------------------------------------------------------------------
# Progress: what an account has finished and found
# ---------------------------------------------------------------------------

async def marks(session: AsyncSession, user_id: str) -> list[TipMark]:
    """Every tip this account has finished, oldest first, whatever its `since` was."""
    rows = await session.scalars(
        select(TipMark).where(TipMark.user_id == user_id).order_by(TipMark.done_at)
    )
    return list(rows)


async def finish(session: AsyncSession, user_id: str, tip_ids: list[str]) -> list[str]:
    """Mark tips done against their current `since`. Returns the ids newly marked.

    Ids that are not a served tip are ignored rather than refused: a tip renamed while
    a window was open is that window's problem for one request, not an error. Finishing
    one twice is a no-op.
    """
    served = {t["id"]: t for t in load()}
    wanted = {i for i in tip_ids if i in served}
    if not wanted:
        return []
    added = []
    for tip_id in sorted(wanted):
        since = served[tip_id]["since"]
        if await session.get(TipMark, (user_id, tip_id, since)):
            continue
        session.add(TipMark(user_id=user_id, tip_id=tip_id, since=since))
        added.append(tip_id)
    return added


async def has_done(session: AsyncSession, user_id: str, tip_id: str) -> bool:
    """Whether this account has finished (or found) a tip, at any `since`.

    For a find that unlocks something: once found, it stays found, even after the tip's
    text is changed and its `since` moved.
    """
    found = await session.scalar(
        select(TipMark.tip_id).where(TipMark.user_id == user_id, TipMark.tip_id == tip_id)
    )
    return found is not None


async def forget(session: AsyncSession, user_id: str, what: str) -> None:
    """Start again. `help` forgets every tip but the hidden ones; `found`, only those.

    Restart tips is for reading the help again, and should not cost anyone what they
    found. Forgetting the finds is its own, separate decision.
    """
    secret = [t["id"] for t in load() if t["group"] == "secret"]
    query = delete(TipMark).where(TipMark.user_id == user_id)
    if what == "found":
        query = query.where(TipMark.tip_id.in_(secret))
    else:
        query = query.where(TipMark.tip_id.not_in(secret))
    await session.execute(query)
