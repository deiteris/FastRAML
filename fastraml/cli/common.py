"""What several verbs share: exit codes, parse options, parsing with a report
of why it failed, and writing a document out.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterable, Sequence

    from fastraml.config import ParserConfig
    from fastraml.errors import RamlError
    from fastraml.parser.entry import ParseOptions
    from fastraml.registry import Raml
    from fastraml.views.graph import Graph

EXIT_OK = 0
EXIT_INVALID = 1


def fail(message: object) -> int:
    """Report why a command failed, on stderr, and the exit code that says so."""
    print(message, file=sys.stderr)
    return EXIT_INVALID


def emit_document(args: argparse.Namespace, text: str, dropped: Iterable[str] = ()) -> int:
    """Write an export to FILE with `-o`, or to stdout without it, then warn
    of each thing the export `dropped`.

    The file is opened UTF-8 with LF newlines regardless of platform, so the
    result does not depend on the shell that ran the command.
    """
    code = EXIT_OK
    if args.output is None:
        print(text, end='')
    else:
        try:
            with open(args.output, 'w', encoding='utf-8', newline='') as handle:
                handle.write(text)
        except OSError as err:
            print(f'{args.output}: {err}', file=sys.stderr)
            code = EXIT_INVALID
    for message in dropped:
        print(f'warning: {message}', file=sys.stderr)
    return code


def rule_overrides(values: Sequence[str], noun: str) -> list[tuple[str, str, str | None]]:
    """Repeatable `--rule ID[=ACTION]`, as `(raw, id, action)`; `None` without `=`.

    Shared by `lint` and `compat`. Each id may be named once: two overrides of
    one rule in a single invocation contradict each other. Each caller applies
    them after the configuration file (docs/16 § 5, docs/18 § 3).
    """
    seen: set[str] = set()
    parsed: list[tuple[str, str, str | None]] = []
    for raw in values:
        rule_id, separator, action = raw.strip().partition('=')
        rule_id, action = rule_id.strip(), action.strip().lower()
        if not rule_id or (separator and not action):
            raise ValueError(f'invalid {noun} override: {raw!r}')
        if rule_id in seen:
            raise ValueError(f'duplicate {noun} override: {rule_id}')
        seen.add(rule_id)
        parsed.append((raw, rule_id, action if separator else None))
    return parsed


def report_invalid(path: str, error: RamlError) -> None:
    """Report a parse failure, and what to do about it where that is knowable."""
    print(f'{path}: invalid', file=sys.stderr)
    print(error, file=sys.stderr)
    hint = _workspace_hint(error)
    if hint:
        print(hint, file=sys.stderr)


def _workspace_hint(error: RamlError) -> str:
    """The `-w` a refused read would have needed, if widening would have helped.

    The root defaults to the entry file's directory, so libraries beside it
    rather than beneath it are refused. `loaders.py` computes `suggested_root`;
    this names the flag.
    """
    for chain in error.chains():
        for frame in chain:
            suggested = frame.info.get('suggested_root') if frame.info else None
            if suggested:
                return (
                    f"hint: the workspace root defaults to the entry file's directory; pass -w {suggested} to widen it"
                )
    return ''


def parse_or_report(
    args: argparse.Namespace,
    path: str | None = None,
    *,
    validate: bool = False,
    retain_source: bool = False,
    retain_text: bool = False,
) -> Raml | None:
    """Parse without projecting, for a verb that needs no graph, or report
    why not. The options are `parse_options`'.

    Validation is off by default, as for every reading verb, so a document
    with a bad example remains navigable (docs/13 § 5).
    """
    from fastraml.errors import RamlError  # noqa: PLC0415 - reading commands only
    from fastraml.parser.entry import parse_from_path  # noqa: PLC0415

    path = path or args.files[0]
    options = parse_options(args, validate=validate, retain_source=retain_source, retain_text=retain_text)
    try:
        return parse_from_path(path, options)
    except RamlError as err:
        report_invalid(path, err)
        return None


def build_or_report(
    args: argparse.Namespace, path: str | None = None, *, retain_text: bool = False
) -> tuple[Graph, Raml] | None:
    """`parse_or_report`, then projected. Returns the graph and the model."""
    from fastraml.views.graph import build_graph  # noqa: PLC0415 - graph commands only

    raml = parse_or_report(args, path, retain_text=retain_text)
    return None if raml is None else (build_graph(raml), raml)


def parse_options(
    args: argparse.Namespace, *, validate: bool = True, retain_source: bool = False, retain_text: bool = False
) -> ParseOptions:
    """`unwrap` is always on; `validate` is on wherever the job is to find faults."""
    from fastraml.loaders import FileLoader  # noqa: PLC0415 - parsing commands only
    from fastraml.parser.entry import ParseOptions  # noqa: PLC0415

    configured: ParserConfig = args.fastraml_config.parser
    return configured.limits(
        ParseOptions(
            unwrap=True,
            validate=validate,
            retain_source=retain_source,
            retain_text=retain_text,
            workspace_root=args.workspace_root or configured.workspace_root,
            file_loader=FileLoader() if args.no_workspace_guard else None,
            http_client=remote_client(args),
        )
    )


def remote_client(args: argparse.Namespace) -> Any:
    """An HTTP client where `-r` or the configuration allows remote includes, else `None`."""
    return new_http_client() if args.remote or args.fastraml_config.parser.remote else None


def new_http_client() -> Any:
    """A client for `-r`: `httpx.Client` or `requests.Session`, whichever is installed.

    fastRAML depends on neither; `HTTPLoader` duck-types `get(url)`.
    """
    for module_name in ('httpx', 'requests'):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        if module_name == 'httpx':
            return module.Client(follow_redirects=True)
        return module.Session()
    message = '--remote needs an HTTP client: pip install "fastraml[http]" (or any httpx / requests already present)'
    raise SystemExit(message)
