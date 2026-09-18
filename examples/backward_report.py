# ruff: noqa: INP001, T201
"""Generate a Markdown compatibility report for two RAML API versions."""

from pathlib import Path

from fastraml import ParseOptions, backward_markdown, parse_from_path

HERE = Path(__file__).parent / 'compatibility'
OPTIONS = ParseOptions(unwrap=True)

old = parse_from_path(HERE / 'v1.raml', OPTIONS)
new = parse_from_path(HERE / 'v2.raml', OPTIONS)

print(backward_markdown(old, new), end='')
