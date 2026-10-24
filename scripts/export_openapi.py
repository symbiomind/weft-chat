#!/usr/bin/env python3
"""
Export the OpenAPI spec to ``docs/openapi.json``.

**Why a generated, committed file instead of hand-written endpoint docs.**

Hand-written API documentation describes the code at the moment somebody wrote it. The
code then moves and the prose does not, and you end up with documentation that actively
lies — which is worse than having none, because people believe it. This file cannot
drift, because it is mechanically derived from the same route signatures and pydantic
models the server actually serves.

Committing it buys the thing a live ``/docs`` page cannot: **a reviewable diff**. A
change to the API surface shows up in ``git diff`` as a change to the contract, in the
same commit that caused it. Adding a field to a response model is no longer an
invisible act.

``tests/test_openapi_spec.py`` fails when this file disagrees with the live app, so
drift is impossible rather than merely discouraged.

Usage (inside the container, where the dependencies live)::

    docker exec dev-weft-service python3 -m scripts.export_openapi           # write
    docker exec dev-weft-service python3 -m scripts.export_openapi --check   # verify
    docker exec dev-weft-service python3 -m scripts.export_openapi --stdout  # print
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Running this as a FILE puts scripts/ on sys.path rather than the project root, so
# `import app` fails. Running it with -m does the right thing. Support both, because
# the failure mode of the wrong one is an ImportError that reads like a broken install
# rather than a wrong command.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DEFAULT_PATH = os.getenv("WEFT_OPENAPI_PATH", "docs/openapi.json")


def build_spec() -> dict:
    """Build the OpenAPI document from the real application.

    Imported lazily so ``--help`` works without a database or config present.

    Note this calls ``create_app()`` rather than importing the module-level ``app``:
    the spec must reflect the route table, not whatever state a previous import left
    behind.
    """
    from app.server import create_app

    return create_app().openapi()


def canonical(spec: dict) -> str:
    """Render the spec deterministically.

    ``sort_keys`` is what makes this a usable golden file. FastAPI builds the document
    in route-registration order, so without sorting, moving an ``include_router`` call
    would produce a large meaningless diff and bury the one line that actually changed.
    Arrays keep their order — only mapping keys are sorted — so ``required: [...]`` and
    parameter lists stay as the schema declares them.
    """
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(path: str = DEFAULT_PATH) -> str:
    text = canonical(build_spec())
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return text


def check(path: str = DEFAULT_PATH) -> bool:
    """True when the committed file matches the live app."""
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as handle:
        return handle.read() == canonical(build_spec())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--path", default=DEFAULT_PATH)
    parser.add_argument(
        "--check", action="store_true",
        help="exit non-zero if the committed spec is stale; write nothing",
    )
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    args = parser.parse_args()

    if args.stdout:
        sys.stdout.write(canonical(build_spec()))
        return 0

    if args.check:
        if check(args.path):
            print(f"{args.path} is up to date.")
            return 0
        print(
            f"{args.path} is STALE — the API surface changed.\n"
            f"Regenerate with: python3 scripts/export_openapi.py\n"
            f"Then READ THE DIFF: it is your API contract changing.",
            file=sys.stderr,
        )
        return 1

    write(args.path)
    print(f"Wrote {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
