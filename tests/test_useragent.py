"""
Tests for app/useragent.py — the label on each box in the signed-in devices list.

The strings are real ones a weft install has seen, plus the impersonators the module's
ordering exists for: every one of these browsers claims to be at least one of the others.
"""

import pytest

from app.useragent import describe


@pytest.mark.parametrize(
    "user_agent, expected",
    [
        (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/152.0.0.0 Safari/537.36",
            ("Chrome", "Linux"),
        ),
        (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/152.0.0.0 Mobile Safari/537.36",
            ("Chrome", "Android"),
        ),
        (
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko)"
            " SamsungBrowser/30.0 Chrome/143.0.0.0 Mobile Safari/537.36",
            ("Samsung Internet", "Android"),
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15"
            " (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
            ("Safari", "iPhone"),
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 26_6_1 like Mac OS X) AppleWebKit/605.1.15"
            " (KHTML, like Gecko) CriOS/153.0.8010.49 Mobile/15E148 Safari/604.1",
            ("Chrome", "iPhone"),
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/152.0.0.0 Safari/537.36 OPR/136.0.0.0",
            ("Opera", "Windows"),
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
            ("Edge", "Windows"),
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
            ("Firefox", "Linux"),
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
            " (KHTML, like Gecko) Version/18.5 Safari/605.1.15",
            ("Safari", "Mac"),
        ),
        ("curl/8.5.0", ("Script", None)),
    ],
)
def test_names_the_browser_and_system(user_agent, expected):
    assert describe(user_agent) == expected


@pytest.mark.parametrize("user_agent", [None, "", "something nobody has heard of"])
def test_says_nothing_rather_than_guessing(user_agent):
    assert describe(user_agent) == (None, None)
