"""`skills`: the agent guides this CLI ships with. Reads no RAML."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from fastraml.cli.arguments import SKILL_DIR
from fastraml.cli.common import EXIT_INVALID, EXIT_OK

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence
    from pathlib import Path

#: The guides this CLI serves, as Markdown inside the package, so `fastraml
#: skills get` always describes the installed build. The installed skill is a
#: stub that fetches them.
_SKILLDATA: Final = 'skilldata'

#: How much of a guide's description `skills list` shows before it truncates.
#: Long enough to route on, short enough that the listing stays a listing.
_DESCRIPTION_WIDTH: Final = 96


#: The guide `skills install` writes when given no name: the discovery stub
#: that points back at `skills get`. `skills list` omits it.
_STUB: Final = 'fastraml'


class _Guide(NamedTuple):
    """One served guide: what `list` needs, plus where to read the rest."""

    name: str
    description: str
    path: Path
    hidden: bool


def _skills(args: argparse.Namespace) -> int:
    guides = _guides()
    if not guides:
        # Reachable only from a broken install -- the data ships in the wheel.
        print(f'no guides found in {_skill_root()}', file=sys.stderr)
        return EXIT_INVALID
    if args.action == 'list':
        return _skills_list(guides, json_mode=args.json)
    if args.action == 'install':
        return _skills_install(guides, args.names or [_STUB], args)
    return _skills_get(guides, args.names, full=args.full, json_mode=args.json)


def _skills_install(guides: dict[str, _Guide], names: Sequence[str], args: argparse.Namespace) -> int:
    """Copy guides into a skills directory, where an agent will discover them.

    Refuses to overwrite without `--force`: the user may have edited an
    installed skill.
    """
    missing = [name for name in names if name not in guides]
    if missing:
        print(f'no such guide: {", ".join(missing)}; try {", ".join(guides)}', file=sys.stderr)
        return EXIT_INVALID

    root = _install_root(args)
    written = []
    for name in names:
        destination = root / name / 'SKILL.md'
        if destination.exists() and not args.force:
            print(f'{destination} exists; pass --force to replace it', file=sys.stderr)
            return EXIT_INVALID
        written.append((destination, guides[name].path.read_text(encoding='utf-8')))

    # All collision checks and reads happen first, so a refusal writes nothing.
    for destination, text in written:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding='utf-8')
        print(f'installed {destination}')
    return EXIT_OK


def _install_root(args: argparse.Namespace) -> Path:
    """Which skills directory to write into: `--dir`, else home with `--user`, else the CWD."""
    from pathlib import Path  # noqa: PLC0415 - this verb only

    if args.dir:
        return Path(args.dir).expanduser()
    return (Path.home() if args.user else Path.cwd()) / SKILL_DIR


def _skill_root() -> Path:
    from pathlib import Path  # noqa: PLC0415 - this verb only

    return Path(__file__).parent.parent / _SKILLDATA


def _guides() -> dict[str, _Guide]:
    """Every guide in the package, in name order."""
    root = _skill_root()
    if not root.is_dir():
        return {}
    found = {}
    for path in sorted(root.iterdir()):
        skill = path / 'SKILL.md'
        if not skill.is_file():
            continue
        front = _frontmatter(skill.read_text(encoding='utf-8'))
        name = str(front.get('name') or path.name)
        # Hidden by name, not by a frontmatter flag: the stub is what gets
        # installed, and another client may read `hidden:` as "keep this from
        # the agent". This keeps it byte-identical to `skills/fastraml/`.
        found[name] = _Guide(name, str(front.get('description') or ''), skill, name == _STUB)
    return found


def _frontmatter(text: str) -> dict[str, Any]:
    """The YAML block a SKILL.md opens with, or an empty mapping.

    Tolerant: a guide whose frontmatter will not parse is still printed, named
    after its directory.
    """
    import yaml  # noqa: PLC0415 - this verb only

    if not text.startswith('---\n'):
        return {}
    block, separator, _ = text[4:].partition('\n---')
    if not separator:
        return {}
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _skills_list(guides: dict[str, _Guide], *, json_mode: bool) -> int:
    """Every guide except the stub, which stays available to `get` and `install`."""
    listed = [guide for guide in guides.values() if not guide.hidden]
    if json_mode:
        import json  # noqa: PLC0415 - only JSON output needs the encoder

        for guide in listed:
            print(json.dumps({'name': guide.name, 'description': guide.description}))
        return EXIT_OK

    width = max(len(guide.name) for guide in listed)
    for guide in listed:
        summary = guide.description
        if len(summary) > _DESCRIPTION_WIDTH:
            # ASCII `...`: a cp1252 Windows console cannot print an ellipsis.
            summary = summary[: _DESCRIPTION_WIDTH - 3].rstrip() + '...'
        print(f'{guide.name:<{width}}  {summary}')
    # Flush stdout first so redirected streams keep their order.
    sys.stdout.flush()
    print("\nRead one with 'fastraml skills get <name>'.", file=sys.stderr)
    return EXIT_OK


def _skills_get(guides: dict[str, _Guide], names: Sequence[str], *, full: bool, json_mode: bool) -> int:
    if not names:
        print(f'skills get needs a name: {", ".join(guides)}', file=sys.stderr)
        return EXIT_INVALID
    missing = [name for name in names if name not in guides]
    if missing:
        # Named, not guessed, as in `navigate._resolve`.
        print(f'no such guide: {", ".join(missing)}; try {", ".join(guides)}', file=sys.stderr)
        return EXIT_INVALID

    wanted = [guides[name] for name in names]
    if json_mode:
        import json  # noqa: PLC0415 - only JSON output needs the encoder

        for guide in wanted:
            record: dict[str, object] = {'name': guide.name, 'content': guide.path.read_text(encoding='utf-8')}
            if full:
                record['references'] = [{'path': name, 'content': text} for name, text in _guide_references(guide)]
            print(json.dumps(record))
        return EXIT_OK

    for index, guide in enumerate(wanted):
        if index:
            print('\n---\n')
        print(guide.path.read_text(encoding='utf-8').rstrip())
        for name, text in _guide_references(guide) if full else ():
            print(f'\n--- {name} ---\n')
            print(text.rstrip())
    return EXIT_OK


def _guide_references(guide: _Guide) -> list[tuple[str, str]]:
    """A guide's `references/` files, in name order. Absent is not an error."""
    folder = guide.path.parent / 'references'
    if not folder.is_dir():
        return []
    return [
        (f'references/{path.name}', path.read_text(encoding='utf-8'))
        for path in sorted(folder.iterdir())
        if path.suffix == '.md'
    ]
