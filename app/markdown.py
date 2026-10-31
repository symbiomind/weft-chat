"""
The small markdown that tips and guide pages are written in, rendered to HTML here.

The client has no markdown renderer (a reply stays plain text, ``web/js/references.js``)
and loads no third-party script, so the HTML arrives ready. The renderer is deliberately
small: paragraphs, lists, **bold**, *italic*, `code` and links. Anything it does not know
stays as the characters it is; a site generator reading the same files can do the full
syntax.

Links the client understands:

- ``tip:<id>`` opens a tip to read.
- ``doc:<id>`` opens a guide page.
- ``https://`` and ``http://`` leave the app, in a new tab.

Any other target goes to ``resolve`` first, if the caller gave one: that is how a guide
page's relative ``.md`` link becomes ``doc:`` or an outbound address. What is still not
one of the above stays as the words it is, so ``javascript:`` never becomes a link.
"""

import html
import re
from collections.abc import Callable

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"\*(.+?)\*")
_ORDERED = re.compile(r"^\d+\.\s+")
_BULLET = re.compile(r"^[-*]\s+")

#: Turns a target the renderer does not know into one it does, or None to leave the
#: link as text. It is given the target as written, already HTML-escaped.
Resolver = Callable[[str], str | None]


def _link(match: re.Match, resolve: Resolver | None) -> str:
    text, target = match.group(1), match.group(2)
    if resolve and not target.startswith(("tip:", "doc:", "https://", "http://")):
        target = resolve(target) or target
    if target.startswith("tip:"):
        return f'<a href="#" data-tip="{target[4:]}">{text}</a>'
    if target.startswith("doc:"):
        return f'<a href="#" data-doc="{target[4:]}">{text}</a>'
    if target.startswith(("https://", "http://")):
        return f'<a href="{target}" target="_blank" rel="noopener noreferrer">{text}</a>'
    # Any other scheme stays as the words it is.
    return match.group(0)


def _inline(text: str, resolve: Resolver | None) -> str:
    """Escape, then mark up. Code spans first, and nothing inside them is touched."""
    parts = text.split("`")
    # An unpaired backtick is a backtick, not the start of code running to the end.
    if len(parts) % 2 == 0:
        parts[-2] += "`" + parts.pop()
    out = []
    for index, part in enumerate(parts):
        safe = html.escape(part, quote=True)
        if index % 2:
            out.append(f"<code>{safe}</code>")
        else:
            safe = _LINK.sub(lambda m: _link(m, resolve), safe)
            safe = _BOLD.sub(r"<strong>\1</strong>", safe)
            safe = _ITALIC.sub(r"<em>\1</em>", safe)
            out.append(safe)
    return "".join(out)


def render_markdown(source: str, resolve: Resolver | None = None) -> str:
    """Paragraphs and lists, with inline marks. Blank lines separate blocks."""
    blocks = [b for b in re.split(r"\n\s*\n", source.strip()) if b.strip()]
    out = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines()]
        if all(_ORDERED.match(line) for line in lines):
            items = "".join(
                f"<li>{_inline(_ORDERED.sub('', line), resolve)}</li>" for line in lines)
            out.append(f"<ol>{items}</ol>")
        elif all(_BULLET.match(line) for line in lines):
            items = "".join(
                f"<li>{_inline(_BULLET.sub('', line), resolve)}</li>" for line in lines)
            out.append(f"<ul>{items}</ul>")
        else:
            out.append(f"<p>{_inline(' '.join(lines), resolve)}</p>")
    return "".join(out)
