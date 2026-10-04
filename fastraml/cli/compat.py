"""`compat`: two versions compared for backward compatibility (docs/16 § 5)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from fastraml.cli.common import EXIT_INVALID, EXIT_OK, emit_document, parse_or_report, rule_overrides

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence


def _compat(args: argparse.Namespace) -> int:
    """What changed, graded by whether it breaks a caller.

    Exits 1 when anything is breaking, so it works as a CI gate. `--json`
    writes one change record per line (docs/16 § 5).
    """
    from fastraml.views.backward import (  # noqa: PLC0415 - compat verb only
        IMPACTS,
        backward,
        backward_types,
        configure,
        record,
        render_markdown,
    )

    models = []
    for path in args.files:
        parsed = parse_or_report(args, path)
        if parsed is None:
            return EXIT_INVALID
        models.append(parsed)

    threshold = IMPACTS.rank('breaking' if args.breaking_only else args.severity)
    try:
        compatibility = _compatibility_rule_overrides(args.fastraml_config.compatibility, args.rule)
        compare = backward_types if args.types else backward
        changes = configure(compare(models[0], models[1]), compatibility)
    except ValueError as err:
        print(f'compat: {err}', file=sys.stderr)
        return EXIT_INVALID
    breaking = sum(change.impact == 'breaking' for change in changes)
    shown = [change for change in changes if IMPACTS.rank(change.impact) <= threshold]

    if args.json:
        import json  # noqa: PLC0415 - only JSON output needs the encoder

        text = ''.join(json.dumps(record(change)) + '\n' for change in shown)
    else:
        text = render_markdown(shown) if shown else ''

    # A failed `-o` write is reported instead of the breaking-change count.
    written = emit_document(args, text)
    if written != EXIT_OK:
        return written
    if breaking and not args.json:
        sys.stdout.flush()
        print(f'{breaking} breaking change{"s" if breaking > 1 else ""}', file=sys.stderr)
    return EXIT_INVALID if breaking else EXIT_OK


def _compatibility_rule_overrides(config: Any, values: Sequence[str]) -> Any:
    from typing import cast, get_args  # noqa: PLC0415 - compatibility CLI only

    from fastraml.config import CompatibilityConfig, CompatibilityRuleSetting, Impact  # noqa: PLC0415 - compat only

    rules = list(config.rules)
    for raw, rule_id, action in rule_overrides(values, 'compatibility rule'):
        if action is None or (action != 'off' and action not in get_args(Impact.__value__)):
            raise ValueError(f'invalid compatibility rule override: {raw!r}')
        # Appended after the file's entries; `configure` lets the last matching
        # entry decide, so these win over the file (docs/16 § 5).
        rules.append(
            CompatibilityRuleSetting(
                id=rule_id,
                impact=None if action == 'off' else cast('Any', action),
                disabled=action == 'off',
            )
        )
    return CompatibilityConfig(rules=tuple(rules))
