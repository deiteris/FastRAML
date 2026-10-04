"""The `fastraml` console script (docs/13-public-api.md § 5).

```
fastraml validate [-v] [--json] FILE...
fastraml info FILE
fastraml graph [--format nt|turtle|dot|json] FILE
fastraml convert openapi [--format yaml|json] FILE.raml
fastraml convert jsonschema FILE.raml [TYPE]
fastraml convert raml FILE.json
fastraml tree [--positions] FILE
fastraml serve [--host H] [--port P] FILE
fastraml list FILE [PATTERN]
fastraml refs|deps FILE NAME
fastraml refs --sites FILE NAME
fastraml show [--depth N] FILE NAME
fastraml compat [--types] [--json] OLD NEW
fastraml join [--title T] [--version V] [--description D] [--base-uri INPUT=URI]... INPUT INPUT...
fastraml query [FILE] (-q SPARQL | -Q FILE.rq | -n NAME | --list | --show NAME)
fastraml lint [--config FILE] [--format human|text|json|summary] FILE...
fastraml skills (list | get NAME... | install [NAME...])
fastraml lsp [--config FILE] [-r]
```

Every parsing verb also takes `--config`, `-w ROOT`, `--no-workspace-guard`
and `-r`; document-producing verbs take `-o FILE`.

Presentation only: this package turns arguments into `ParseOptions`, calls an
entry point or a view, and formats the result. `validate --json` writes one
`RamlError.to_dict()` record per file and continues past failing files.

`arguments` builds the parser and names each verb's handler; `common` holds
what several verbs share; every other module is one verb group, imported only
when one of its verbs runs.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from fastraml.cli.arguments import COMMANDS, build_parser
from fastraml.cli.common import EXIT_INVALID, EXIT_OK

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Callable, Sequence

__all__ = ['EXIT_INVALID', 'EXIT_OK', 'main']


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(args, 'config'):
        from fastraml.config import load_config  # noqa: PLC0415 - only parsing commands carry configuration

        try:
            args.fastraml_config = load_config(args.config)
        except (OSError, TypeError, ValueError) as err:
            print(f'config: {err}', file=sys.stderr)
            return EXIT_INVALID
    return _handler(args.command)(args)


def _handler(command: str) -> Callable[[Namespace], int]:
    """The function that runs `command`, importing its module only now."""
    from importlib import import_module  # noqa: PLC0415 - dispatch only

    module, _, function = COMMANDS[command].partition(':')
    handler: Callable[[Namespace], int] = getattr(import_module(f'fastraml.cli.{module}'), function)
    return handler
