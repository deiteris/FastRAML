"""The conformance corpus: one set of questions, answered in every language.

`fastraml/views/bindings/conformance/` holds the corpus. Each backend ships a
driver beside its runtime half — `static/conform.{py,ts,go}` — which reads the
corpus, runs *its* language's reading of the contract, and writes the answers as
JSON. The drivers hold no expectations; `tests/unit/test_conformance.py` diffs
what each one printed against `cases.json`.

Every answer is a string or a list of strings. The three languages differ on
what a decoded JSON value is — Go's `Json` is raw bytes, Python's is a decoded
object — so the questions ask only for addresses and labels.

Expected answers are generated from the Python runtime, so the corpus checks
that the three languages agree and locks each against silent change. It is not
an independent oracle for Python; `TestTheGeneratedWalkReachesEveryShape` is,
deriving the expected shape set from the raw JSON without any reader.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import TYPE_CHECKING, Any, Final

from .python import vendored

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ['CORPUS', 'SOURCES', 'answers', 'build_corpus', 'load_corpus', 'main']

CORPUS: Final = pathlib.Path(__file__).parent / 'conformance'

#: The RAML each tree is projected from. A tree with no source is a tree that
#: cannot be checked for staleness, which is the failure this corpus exists to
#: catch elsewhere. `bookstore.json` comes from `fixtures/sample/api.raml`,
#: which is outside the package; `tests/unit/test_conformance.py` checks both.
SOURCES: Final = CORPUS / 'sources'

#: Envelopes a reader must refuse. The first is the mistake worth catching by
#: name — pointing a tree consumer at `fastraml graph --format json`, which is
#: also JSON and also has nodes (docs/16 § 11.9).
REFUSE: Final[tuple[dict[str, Any], ...]] = (
    {'format': 'fastraml-graph', 'format_version': 1, 'view': 'effective'},
    {'format': 'fastraml-tree', 'format_version': 2, 'view': 'effective'},
    {'format': 'fastraml-tree', 'format_version': 1, 'view': 'source'},
    {'format': 'fastraml-tree', 'view': 'effective'},
    {},
)


def load_corpus() -> dict[str, Any]:
    """The corpus as the drivers read it."""
    loaded: dict[str, Any] = json.loads((CORPUS / 'cases.json').read_text(encoding='utf-8'))
    return loaded


def build_corpus() -> dict[str, Any]:
    """The corpus, rebuilt from the trees beside it.

    The golden idiom: a test regenerates and compares, so a change to the
    projection or to the walk moves the file and is read as a diff rather than
    discovered as three languages disagreeing.
    """
    cases = {}
    for tree in sorted((CORPUS / 'trees').glob('*.json')):
        document = json.loads(tree.read_text(encoding='utf-8'))
        cases[tree.stem] = {'tree': f'trees/{tree.name}', 'expected': answers(document)}
    return {'format': 'fastraml-tree-conformance', 'format_version': 1, 'cases': cases, 'refuse': list(REFUSE)}


def answers(document: dict[str, Any]) -> dict[str, Any]:
    """Every question the corpus asks of one document, answered.

    Answered through the *vendored* Python runtime, which is the same file every
    Python consumer gets, so the corpus measures the shipped artifact rather
    than a private path through the parser.
    """
    walk = vendored()
    tree = walk.Tree.of(document)
    probes = list(_probes(tree, document))
    return {
        'shapes': [shape.get('id') for shape in tree.shapes()],
        'probes': probes,
        'children': {address: [_label(node) for node in tree.children(tree.at(address))] for address in probes},
        'resolve': {address: _address(tree.resolve({'$ref': address})) for address in probes},
        'content': {address: _address(tree.content(found)) for address in probes if (found := tree.at(address))},
    }


def _probes(tree: Any, document: dict[str, Any]) -> Iterator[str]:
    """The addresses every driver is asked about, in a fixed order.

    Declarations, because they are the addresses a `$ref` names, plus every
    expanded shape that is a `json` type or holds a recursion marker — the two
    constructs a walk is most likely to get wrong and least likely to meet by
    accident in a small document.
    """
    seen: list[str] = []
    for by_file in (document['types'], document['annotation_types']):
        for declarations in by_file.values():
            for node in declarations.values():
                address = node.get('$ref') or node.get('id')
                if address and address not in seen and tree.at(address) is not None:
                    seen.append(address)
    for shape in tree.shapes():
        address = shape.get('id')
        if not address or address in seen:
            continue
        marked = any(child.get('type') == 'recursive' for child in tree.children(shape) if isinstance(child, dict))
        if shape.get('type') == 'json' or marked:
            seen.append(address)
    yield from seen


def _label(node: Any) -> str:
    """One child, as which of the three constructs it is.

    A link and a marker are named rather than followed: naming them is what
    makes a driver that silently expands one fail here.
    """
    if not isinstance(node, dict):
        return 'none'
    if set(node) == {'$ref'}:
        return f'ref:{node["$ref"]}'
    if node.get('type') == 'recursive':
        return f'recursive:{node.get("id") or ""}'
    return f'shape:{node.get("id") or ""}'


def _address(found: Any) -> str | None:
    return found.get('id') if isinstance(found, dict) else None


def main() -> None:
    """Rebuild the corpus from the trees beside it."""
    written = CORPUS / 'cases.json'
    written.write_text(json.dumps(build_corpus(), indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    sys.stdout.write(f'wrote {written.resolve()}\n')


if __name__ == '__main__':
    main()
