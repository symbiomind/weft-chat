"""
A user-agent string, said the way a person would: "Firefox", "Android".

For the signed-in devices list and nothing else. It is a label for a
person deciding "is that one mine?", not a fingerprint and not a security boundary — a
client can send any string it likes, and the list shows the IP beside it for that
reason.

Parsed on read rather than stored, so a better table here relabels every device at once
and no migration ever has to carry the parser.

**Order is the whole trick.** Nearly every browser claims to be several others for
compatibility: Edge says Chrome, Chrome says Safari, Samsung Internet says Chrome, every
iOS browser says Safari. So the tables are checked top to bottom and the most specific
name comes first. Add new rows above whatever they impersonate.
"""

import re

#: (pattern, name). First match wins.
_BROWSERS: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(pattern), name)
    for pattern, name in (
        (r"\bEdg(?:e|A|iOS)?/", "Edge"),
        (r"\b(?:OPR|Opera|OPiOS)/", "Opera"),
        (r"\bSamsungBrowser/", "Samsung Internet"),
        (r"\b(?:Firefox|FxiOS)/", "Firefox"),
        (r"\bCriOS/", "Chrome"),
        (r"\b(?:Chromium|Chrome)/", "Chrome"),
        (r"\bVersion/[\d.]+.*\bSafari/", "Safari"),
        (r"\b(?:curl|Wget|python-requests|python-httpx|okhttp)/", "Script"),
    )
)

_SYSTEMS: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(pattern), name)
    for pattern, name in (
        (r"\b(?:iPhone|iPod)\b", "iPhone"),
        (r"\biPad\b", "iPad"),
        # Android before Linux: every Android string says Linux too.
        (r"\bAndroid\b", "Android"),
        (r"\bCrOS\b", "ChromeOS"),
        (r"\bWindows\b", "Windows"),
        # iPadOS asks for desktop sites by default and calls itself a Mac. Nothing in
        # the string tells them apart, so "Mac" it is.
        (r"\bMac OS X\b|\bMacintosh\b", "Mac"),
        (r"\bLinux\b|\bX11\b", "Linux"),
    )
)


def describe(user_agent: str | None) -> tuple[str | None, str | None]:
    """``(browser, system)``, either of which is None when nothing matched."""
    if not user_agent:
        return None, None
    browser = next((name for pattern, name in _BROWSERS if pattern.search(user_agent)), None)
    system = next((name for pattern, name in _SYSTEMS if pattern.search(user_agent)), None)
    return browser, system
