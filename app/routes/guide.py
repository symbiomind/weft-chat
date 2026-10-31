"""
The Guide: what the Guide screen lists, every page with its html ready.

At ``/api/guide``, not ``/api/docs``: that path is the API's own reference, served when
``security.expose_docs`` is on.

The pages are short (each is the ``content:`` of its file, not the whole page), so one
request carries them all, as ``/api/tips`` does, and a ``doc:`` link opens without
another. Signed in, like tips. See ``app/docs.py`` for the files and the rules.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import deps, docs
from app.models import User

router = APIRouter(
    prefix="/api/guide",
    tags=["guide"],
    dependencies=[Depends(deps.require_db_healthy), Depends(deps.require_csrf)],
)


class PageOut(BaseModel):
    #: Stable: `doc:` links and the client open a page by this.
    id: str
    title: str
    #: The list row's second line, if the page has one.
    summary: str | None
    #: The version the page describes.
    since: str
    #: The page's short version (its `content:`), rendered.
    html: str
    #: The whole page, to read outside the app; null when no docs url is set.
    url: str | None


class GuideOut(BaseModel):
    #: The guide folder's own intro (its `_index.md` content), rendered, if any.
    intro: str | None
    #: In order: by weight, then title.
    pages: list[PageOut]


@router.get("", response_model=GuideOut)
async def guide(user: User = Depends(deps.active_user)) -> GuideOut:
    """Every guide page, in order, ready to show."""
    return GuideOut(**docs.load())
