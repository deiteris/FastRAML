"""Differential test: our composer against a YAML 1.2 implementation.

RAML 1.0 is defined over YAML 1.2. PyYAML implements YAML 1.1, so its implicit
resolvers disagree with the spec — and with go-raml, whose `gopkg.in/yaml.v3`
resolves the 1.2 core schema. `pyraml.yamlnode` replaces the resolver table to
close that gap.

This module checks that claim against an oracle rather than against a reading of
the table: `ruamel.yaml` with `typ='safe', pure=True`, which implements YAML 1.2
resolution. Every document in the TCK corpus, plus a table of hand-written
scalar forms, is composed twice and the two node trees are compared on shape,
tag and text.

Ruamel is a **dev dependency only**. It never ships, and pyRAML never imports it
outside this file. The pure-Python loader is deliberate: ruamel's C extension
carries a pre-0.2.2 libyaml scanner that rejects `[ http://example.com ]`, which
is valid YAML 1.2 and appears in real RAML.

Divergences that are known and accepted are listed in `KNOWN_DIVERGENCES` with
the reason. Anything else fails. See docs/03-yaml-and-io.md section 2.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest

from pyraml.yamlnode import Node, NodeKind, compose

if TYPE_CHECKING:
    from pathlib import Path

ruamel = pytest.importorskip('ruamel.yaml', reason='the YAML 1.2 oracle is a dev dependency')

pytestmark = pytest.mark.conformance


class Divergence(NamedTuple):
    """One disagreement between our composer and the oracle."""

    path: str
    ours: str
    oracle: str

    def __str__(self) -> str:
        return f'at {self.path}: ours={self.ours} oracle={self.oracle}'


#: Scalar forms where YAML 1.1 and 1.2 disagree, plus the neighbours that must
#: not move. The oracle decides what each should be; this list decides what gets
#: asked. See docs/05-type-model.md section 3.1 for why exactness matters.
SCALAR_FORMS = [
    # YAML 1.1 booleans that YAML 1.2 reads as strings.
    'yes',
    'no',
    'on',
    'off',
    'y',
    'n',
    'Yes',
    'OFF',
    # The booleans that survive.
    'true',
    'false',
    'True',
    'FALSE',
    # Base-60, dropped in YAML 1.2. `12:30:00` is a time-only example.
    '12:30:00',
    '190:20:30.15',
    '-12:30',
    # Integers.
    '0',
    '42',
    '-7',
    '+7',
    '0x1f',
    '0o17',
    '017',
    '08',
    '09',
    '1_000',
    '0b1010',
    # Floats.
    '1.5',
    '-0.0',
    '1e3',
    '1.2e-3',
    '.5',
    '.inf',
    '-.inf',
    '.nan',
    # Null, and the empty value.
    'null',
    'Null',
    'NULL',
    '~',
    '',
    # Timestamps: RAML wants the written form, so the tag matters less than the
    # text, but a disagreement here would still be a surprise.
    '2015-05-23',
    '2015-05-23T15:00:00Z',
    # Strings that merely look like something else.
    'abc',
    '1.2.3',
    '0x',
    '1_',
    'e10',
    '1:2:3:4',
]

#: Documents that exercise structure rather than scalars.
STRUCTURAL_DOCUMENTS = [
    'a: 1\nb: 2\n',
    'a:\n  - 1\n  - two\n',
    'a: [1, two, false]\n',
    'a: {x: 1, y: no}\n',
    'a: !include other.raml\n',
    "a: 'no'\n",
    'a: "12:30:00"\n',
    'a: |\n  no\n',
    'a: >\n  no\n',
    'a: &anc 1\nb: *anc\n',
    'a: [ http://example.com ]\n',
    'a: {}\n',
    'a: []\n',
    'a:\n',
    '? [1, 2]\n: value\n',
]

#: Fixtures whose divergence is understood and accepted. Keyed by the reason, so
#: an entry cannot be added without stating one.
KNOWN_DIVERGENCES: dict[str, str] = {}

#: Corpus documents that one side or the other refuses to compose at all. The
#: TCK ships deliberately malformed fixtures, so this is never zero — but it is
#: bounded, because a change that silently stopped comparing half the corpus
#: would otherwise look like a pass.
MAX_UNCOMPARABLE = 6


def _oracle_compose(text: str) -> Any:
    """Compose with the YAML 1.2 oracle, or `None` for an empty document."""
    loader = ruamel.YAML(typ='safe', pure=True)
    return loader.compose(io.StringIO(text))


def _oracle_shape(node: Any, path: str = '$') -> dict[str, tuple[str, str]]:
    """Flatten an oracle node tree to `path -> (tag, text)`."""
    flat: dict[str, tuple[str, str]] = {}
    if node is None:
        return flat
    tag = str(node.tag)
    if hasattr(node, 'value') and isinstance(node.value, list):
        flat[path] = (_short(tag), '')
        if tag == 'tag:yaml.org,2002:map':
            for index, (key, value) in enumerate(node.value):
                label = _label(key.value if not isinstance(key.value, list) else None, index)
                flat.update(_oracle_shape(key, f'{path}.{label}#key'))
                flat.update(_oracle_shape(value, f'{path}.{label}'))
        else:
            for index, item in enumerate(node.value):
                flat.update(_oracle_shape(item, f'{path}[{index}]'))
    else:
        flat[path] = (_short(tag), str(node.value))
    return flat


def _our_shape(node: Node, path: str = '$') -> dict[str, tuple[str, str]]:
    """Flatten our node tree the same way."""
    flat: dict[str, tuple[str, str]] = {}
    if node.kind is NodeKind.MAPPING:
        flat[path] = (node.tag, '')
        for index in range(0, len(node.content) - 1, 2):
            key, value = node.content[index], node.content[index + 1]
            label = _label(key.value if key.kind is NodeKind.SCALAR else None, index // 2)
            flat.update(_our_shape(key, f'{path}.{label}#key'))
            flat.update(_our_shape(value, f'{path}.{label}'))
    elif node.kind is NodeKind.SEQUENCE:
        flat[path] = (node.tag, '')
        for index, item in enumerate(node.content):
            flat.update(_our_shape(item, f'{path}[{index}]'))
    else:
        flat[path] = (node.tag, node.value)
    return flat


def _label(key_text: str | None, index: int) -> str:
    """A path segment for a mapping entry.

    A complex key — `? [1, 2]` — has no text, so the entry is addressed by
    position. Without this the two trees would be compared under different
    paths and every entry would look like a divergence.
    """
    return key_text if key_text is not None else f'#{index}'


def _short(tag: str) -> str:
    prefix = 'tag:yaml.org,2002:'
    return '!!' + tag[len(prefix) :] if tag.startswith(prefix) else tag


def diverge(text: str) -> list[Divergence]:
    """Every point where our composition disagrees with the oracle."""
    ours = _our_shape(compose(text, uri='file:///oracle.raml'))
    theirs = _oracle_shape(_oracle_compose(text))
    if not theirs:
        # An empty document: we synthesise an empty mapping, the oracle gives
        # nothing. That difference is ours by design (docs/03 section 3).
        return []
    found: list[Divergence] = []
    for path in sorted(set(ours) | set(theirs)):
        mine = ours.get(path)
        yours = theirs.get(path)
        if mine != yours:
            found.append(Divergence(path, str(mine), str(yours)))
    return found


class TestScalarResolution:
    @pytest.mark.parametrize('form', SCALAR_FORMS, ids=lambda f: f or 'empty')
    def test_a_scalar_resolves_as_yaml_1_2_says(self, form: str):
        assert diverge(f'v: {form}\n') == []

    @pytest.mark.parametrize('form', SCALAR_FORMS, ids=lambda f: f or 'empty')
    def test_the_same_form_quoted_is_always_a_string(self, form: str):
        # Quoting suppresses implicit resolution in both implementations; if it
        # did not, replacing the resolver table could change a quoted value.
        assert diverge(f"v: '{form}'\n") == []

    @pytest.mark.parametrize('form', SCALAR_FORMS, ids=lambda f: f or 'empty')
    def test_the_same_form_as_a_mapping_key(self, form: str):
        assert diverge(f'{form or "empty"}: 1\n') == []

    @pytest.mark.parametrize('form', SCALAR_FORMS, ids=lambda f: f or 'empty')
    def test_the_same_form_inside_a_flow_sequence(self, form: str):
        if ',' in form or '[' in form:
            pytest.skip('not a single flow item')
        assert diverge(f'v: [{form}]\n') == []


class TestStructure:
    @pytest.mark.parametrize('document', STRUCTURAL_DOCUMENTS, ids=range(len(STRUCTURAL_DOCUMENTS)))
    def test_structure_and_tags_agree(self, document: str):
        assert diverge(document) == []


class TestCorpus:
    """Every TCK document, composed twice.

    This is the part that finds what a hand-written table misses: real RAML
    written by people who were not thinking about YAML versions.
    """

    def test_every_tck_document_composes_identically(self, tck_documents: list[Path]):
        if not tck_documents:
            pytest.skip('no TCK corpus; set PYRAML_TCK_DIR')
        mismatched: dict[str, list[Divergence]] = {}
        uncomparable = 0
        for path in tck_documents:
            try:
                text = path.read_text(encoding='utf-8-sig')
                found = diverge(text)
            except Exception:
                uncomparable += 1
                continue
            if found and path.name not in KNOWN_DIVERGENCES:
                mismatched[str(path)] = found
        assert not mismatched, _report(mismatched)
        compared = len(tck_documents) - uncomparable
        assert uncomparable <= MAX_UNCOMPARABLE, (
            f'{uncomparable} documents could not be compared (limit {MAX_UNCOMPARABLE}); '
            f'only {compared} of {len(tck_documents)} were actually checked'
        )


def _report(mismatched: dict[str, list[Divergence]]) -> str:
    lines = [f'{len(mismatched)} document(s) diverge from YAML 1.2:']
    for path, found in sorted(mismatched.items())[:20]:
        lines.append(f'  {path}')
        lines.extend(f'    {item}' for item in found[:5])
    return '\n'.join(lines)
