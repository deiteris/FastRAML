"""The generated TypeScript contract — docs/16-graph.md § 11.11.

Two questions, and the second is the one that cannot be argued with.

Is the checked-in file what the generator produces? That is the golden idiom,
and it is needed because the file is read by a build this suite never runs.

And does the generator agree with the *emitter*? Only output can answer that. A
generator reads source and can be wrong about what running it does, so a
document declaring every kind is projected and its keys are checked against the
file. Law 19 in `tests/tck/test_properties.py` asks the same of the corpus.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

from fastraml import ParseOptions, parse_from_path
from fastraml.views.bindings import DESTINATION, typescript
from fastraml.views.tree import build_tree

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def declared_members() -> dict[str, set[str]]:
    """Every member of every interface in the generated file, by interface."""
    text = (ROOT / DESTINATION).read_text(encoding='utf-8')
    out: dict[str, set[str]] = {}
    for block in re.finditer(r'export interface (\w+) \{(.*?)\n\}', text, re.DOTALL):
        name, body = block.group(1), block.group(2)
        out[name] = set(re.findall(r'^\s{2}(\$?\w+)\??:', body, re.MULTILINE))
    return out


class TestTheCheckedInFileIsGenerated:
    def test_regenerating_changes_nothing(self):
        # The golden idiom, for the same reason the goldens use it: the file is
        # read by a build this suite does not run, so nothing else would notice
        # it drifting from its source.
        current = (ROOT / DESTINATION).read_text(encoding='utf-8')
        assert current == typescript(), f'run `python -m fastraml.views.bindings` -- {DESTINATION} is stale'

    def test_the_module_writes_the_file_it_names(self):
        # `python -m` is the documented way to regenerate, so it is worth one
        # test: an entry point that raises on import is a broken instruction.
        result = subprocess.run(
            [sys.executable, '-m', 'fastraml.views.bindings'],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert DESTINATION.rsplit('/', 1)[-1] in result.stdout


DOCUMENT = """#%RAML 1.0
title: Every kind
types:
  Bounded:
    type: number
    minimum: 0.5
    maximum: 9.5
    multipleOf: 0.01
  Counted:
    type: integer
    minimum: 1
    maximum: 10
  Text:
    type: string
    minLength: 1
    maxLength: 8
    pattern: ^a
  Listed:
    type: array
    items: Text
    minItems: 1
    maxItems: 3
    uniqueItems: true
  Structured:
    type: object
    minProperties: 1
    maxProperties: 4
    additionalProperties: false
    discriminator: kind
    properties:
      kind: string
      /^x-/: string
  Either: string | number
  Upload:
    type: file
    fileTypes: [image/png]
    minLength: 1
    maxLength: 2
  When:
    type: datetime
    format: rfc3339
  Chain:
    properties:
      next?: Chain
"""


class TestEveryKindLandsInTheContract:
    """One document declaring every kind, checked key by key against the file.

    The corpus form of this is law 19 in `tests/tck/test_properties.py`, which
    needs a checkout. This one always runs, so a facet added to a kind fails
    here on any machine.
    """

    @pytest.fixture
    def keys(self, workspace):
        root = workspace({'api.raml': DOCUMENT})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        seen: set[str] = set()
        _observe(build_tree(raml), seen)
        return seen

    def test_every_key_that_arrives_is_declared(self, keys):
        declared = declared_members()['Shape'] | {'head'}
        assert not keys - declared, f'emitted but not in the contract: {sorted(keys - declared)}'

    def test_the_document_reaches_the_facets_it_was_written_for(self, keys):
        # A vacuous pass is how a corpus-shaped test fails: an empty set of
        # observed keys satisfies the check above and proves nothing.
        assert {'minimum', 'multiple_of', 'pattern', 'items', 'properties', 'any_of', 'file_types'} <= keys

    def test_a_bound_arrives_as_an_exact_decimal_string(self, workspace):
        # The contract says so in a comment; this is what makes the comment
        # true. It read `1/100` and `1/2` -- exact, and neither what the author
        # wrote nor anything a consumer could show without long division.
        root = workspace({'api.raml': DOCUMENT})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        bounded = build_tree(raml)['types']['api.raml']['Bounded']
        assert bounded['multiple_of'] == '0.01'
        assert bounded['minimum'] == '0.5'


def _observe(node: object, into: set[str]) -> None:
    """The keys of every mapping that is a shape.

    A mapping carrying `id`, `name` and `type` is one -- the three keys
    `shape()` always writes, and the reason it always writes them.
    """
    if isinstance(node, dict):
        if {'id', 'name', 'type'} <= set(node):
            into.update(node)
        for value in node.values():
            _observe(value, into)
    elif isinstance(node, list):
        for item in node:
            _observe(item, into)


#: What `npm run sample` writes, and what `smoke` and `shots` then read.
SAMPLE = 'viewer/public/api.json'
#: The document itself is a repo-level fixture, not the viewer's: `fastmcp-raml`
#: measures its route building against the same file.
SAMPLE_SOURCE = 'fixtures/sample/api.raml'
SAMPLE_ROOT = 'fixtures'


class TestTheViewerSampleIsNotStale:
    """The viewer's checked-in data, held to the contract's own standard.

    `tree.d.ts` is gate-checked and the data beside it was not, so a change to
    the projection left the viewer's two gates -- `smoke` and `shots` -- running
    against the *previous* shape of the tree. They pass, because a page rendered
    from old data is still a page; what they stop measuring is the emitter.

    Found the way it would be: wrapping an example in a record left
    `items.example` a bare string in the committed sample, so the type page
    dropped it and only the reachability check noticed, by a route that had
    nothing to do with examples.

    Compared as parsed JSON rather than as text: the file is written through a
    shell redirect, so its line endings are the platform's and are not the
    contract.
    """

    def test_regenerating_changes_nothing(self):
        import json

        from fastraml.views.tree import build_tree

        raml = parse_from_path(
            ROOT / SAMPLE_SOURCE,
            ParseOptions(unwrap=True, workspace_root=ROOT / SAMPLE_ROOT),
        )
        current = json.loads((ROOT / SAMPLE).read_text(encoding='utf-8'))
        assert current == build_tree(raml), f'run `npm run sample` in viewer/ -- {SAMPLE} is stale'
