"""The `--update-golden` flag."""

from __future__ import annotations


def pytest_addoption(parser) -> None:
    parser.addoption(
        '--update-golden',
        action='store_true',
        default=False,
        help='rewrite every expected.json / expected.pos.json from the current model',
    )
