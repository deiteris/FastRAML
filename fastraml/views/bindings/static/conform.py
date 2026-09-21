"""Answer the conformance corpus through this language's reading of the tree.

Copied verbatim by `python -m fastraml.views.bindings python --conform FILE`.
Do not edit the copy. Edit `fastraml/views/bindings/static/conform.py`.

    python -m <package>.conform <corpus-directory>

Reads `cases.json`, runs `walk.py` over each tree it names, and writes the
answers to stdout as JSON. It holds no expectations: the corpus has those, and
`tests/unit/test_conformance.py` does the comparing.

`conform.ts` and `conform.go` are the same file. Each answer is a string or a
list of strings: the three languages differ on what a decoded JSON value is and
agree on what an address is.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

from .walk import Tree, UnreadableTree


def answers(document: dict[str, Any], probes: list[str]) -> dict[str, Any]:
    """Every question the corpus asks of one document."""
    tree = Tree.of(document)
    return {
        'shapes': [shape.get('id') for shape in tree.shapes()],
        'probes': probes,
        'children': {address: [label(node) for node in tree.children(tree.at(address))] for address in probes},
        'resolve': {address: address_of(tree.resolve({'$ref': address})) for address in probes},
        'content': {address: address_of(tree.content(found)) for address in probes if (found := tree.at(address))},
    }


def label(node: Any) -> str:
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


def address_of(found: Any) -> str | None:
    return found.get('id') if isinstance(found, dict) else None


def refuses(envelope: Any) -> bool:
    try:
        Tree.of(envelope)
    except UnreadableTree:
        return True
    return False


def main(argv: list[str]) -> int:
    corpus = pathlib.Path(argv[1])
    cases = json.loads((corpus / 'cases.json').read_text(encoding='utf-8'))
    out: dict[str, Any] = {'cases': {}, 'refuse': [refuses(one) for one in cases['refuse']]}
    for name, case in cases['cases'].items():
        document = json.loads((corpus / case['tree']).read_text(encoding='utf-8'))
        out['cases'][name] = answers(document, case['expected']['probes'])
    json.dump(out, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
