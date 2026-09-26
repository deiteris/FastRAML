"""The occurrence index (docs/16 § 9): where each name is written, and what it names."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fastraml import ParseOptions, parse_from_path, parse_lenient
from fastraml.uris import path_to_file_uri
from fastraml.views.occurrences import Kind, Role, build_occurrences

if TYPE_CHECKING:
    from pathlib import Path

    from fastraml.registry import Raml
    from fastraml.views.occurrences import Occurrence, Occurrences

API = """#%RAML 1.0
title: Demo
description: !include readme.md
uses:
  lib: lib.raml
types:
  Money: !include money.raml
  User:
    type: object
    facets:
      tier: string
    properties:
      name: string
      tags: string[]
      friend?: lib.Person
annotationTypes:
  note: string
securitySchemes:
  basic.v1:
    type: Basic Authentication
resourceTypes:
  collection:
    get:
      responses:
        200:
          body:
            application/json:
              type: User
traits:
  paged:
    queryParameters:
      page: integer
/users:
  type: collection
  is: [paged, lib.audited]
  securedBy: [basic.v1]
  (note): hi
  get:
  /{id}:
    type: collection
"""

LIBRARY = """#%RAML 1.0 Library
types:
  Person:
    properties:
      age: integer
traits:
  audited:
    headers:
      X-Audit: string
"""

MONEY = """#%RAML 1.0 DataType
type: number
"""


def _write(tmp_path: Path, api: str = API) -> Path:
    (tmp_path / 'lib.raml').write_text(LIBRARY, encoding='utf-8')
    (tmp_path / 'money.raml').write_text(MONEY, encoding='utf-8')
    (tmp_path / 'readme.md').write_text('Read me.', encoding='utf-8')
    entry = tmp_path / 'api.raml'
    entry.write_text(api, encoding='utf-8')
    return entry


def _parsed(tmp_path: Path, api: str = API) -> tuple[Raml, Occurrences, str]:
    entry = _write(tmp_path, api)
    raml = parse_from_path(entry, ParseOptions(unwrap=True, retain_source=True))
    return raml, build_occurrences(raml), path_to_file_uri(entry)


def _where(text: str, needle: str, nth: int = 0) -> tuple[int, int]:
    """The 1-based line and column of the `nth` `needle` in `text`."""
    offset = -1
    for _ in range(nth + 1):
        offset = text.index(needle, offset + 1)
    line = text.count('\n', 0, offset) + 1
    return line, offset - text.rfind('\n', 0, offset)


def _found(occurrences: Occurrences, uri: str) -> set[tuple[Role, Kind, str, int, int]]:
    return {(o.role, o.kind, o.written, o.span.line, o.span.column) for o in occurrences.in_file(uri)}


def _only(found: list[Occurrence]) -> Occurrence:
    assert len(found) == 1, found
    return found[0]


class TestEachNameIsAnOccurrence:
    @pytest.mark.parametrize(
        ('expected', 'site'),
        [
            pytest.param((Role.DEFINITION, Kind.TYPE, 'User'), ('User:', 0), id='type'),
            pytest.param((Role.DEFINITION, Kind.ANNOTATION_TYPE, 'note'), ('note:', 0), id='annotation type'),
            pytest.param((Role.DEFINITION, Kind.TRAIT, 'paged'), ('paged:', 0), id='trait'),
            pytest.param((Role.DEFINITION, Kind.RESOURCE_TYPE, 'collection'), ('collection:', 0), id='resource type'),
            pytest.param((Role.DEFINITION, Kind.SECURITY_SCHEME, 'basic.v1'), ('basic.v1:', 0), id='security scheme'),
            pytest.param((Role.DEFINITION, Kind.LIBRARY, 'lib'), ('lib:', 0), id='uses entry'),
            pytest.param((Role.DEFINITION, Kind.PROPERTY, 'name'), ('name:', 0), id='property'),
            pytest.param((Role.DEFINITION, Kind.PROPERTY, 'friend'), ('friend?', 0), id='optional property'),
            pytest.param((Role.DEFINITION, Kind.FACET, 'tier'), ('tier:', 0), id='facet'),
            pytest.param((Role.REFERENCE, Kind.TYPE, 'User'), ('User\n', 0), id='type name'),
            pytest.param((Role.ALIAS_PREFIX, Kind.LIBRARY, 'lib'), ('lib.Person', 0), id='alias prefix'),
            pytest.param((Role.REFERENCE, Kind.TYPE, 'Person'), ('Person', 0), id='qualified type name'),
            pytest.param((Role.BUILTIN, Kind.TYPE, 'string'), ('string[]', 0), id='built-in in an expression'),
            pytest.param((Role.REFERENCE, Kind.RESOURCE_TYPE, 'collection'), ('collection', 1), id='type:'),
            pytest.param((Role.REFERENCE, Kind.TRAIT, 'paged'), ('paged', 1), id='is:'),
            pytest.param((Role.ALIAS_PREFIX, Kind.LIBRARY, 'lib'), ('lib.audited', 0), id='qualified is: prefix'),
            pytest.param((Role.REFERENCE, Kind.TRAIT, 'audited'), ('audited', 0), id='qualified is:'),
            pytest.param((Role.REFERENCE, Kind.SECURITY_SCHEME, 'basic.v1'), ('basic.v1]', 0), id='securedBy:'),
            pytest.param((Role.REFERENCE, Kind.ANNOTATION_TYPE, 'note'), ('note)', 0), id='annotation'),
            pytest.param((Role.LINK, Kind.FILE, 'lib.raml'), ('lib.raml', 0), id='uses: value'),
            pytest.param((Role.LINK, Kind.FILE, 'money.raml'), ('money.raml', 0), id='!include'),
        ],
    )
    def test_the_name_is_found_where_it_is_written(self, tmp_path, expected, site):
        _, occurrences, uri = _parsed(tmp_path)
        assert (*expected, *_where(API, *site)) in _found(occurrences, uri)

    def test_nothing_is_dropped_from_a_document_the_law_can_read(self, tmp_path):
        _, occurrences, _ = _parsed(tmp_path)
        assert occurrences.dropped == ()

    def test_a_declaration_in_a_library_is_found_in_its_file(self, tmp_path):
        _, occurrences, uri = _parsed(tmp_path)
        library = uri.rsplit('/', 1)[0] + '/lib.raml'
        assert (Role.DEFINITION, Kind.TYPE, 'Person', *_where(LIBRARY, 'Person')) in _found(occurrences, library)


class TestTargets:
    def test_a_reference_meets_its_definition_on_the_target(self, tmp_path):
        raml, occurrences, uri = _parsed(tmp_path)
        person = raml.types_in(uri.rsplit('/', 1)[0] + '/lib.raml')['Person']
        roles = sorted(o.role for o in occurrences.of(person.id))
        assert roles == [Role.DEFINITION, Role.REFERENCE]

    def test_a_prefix_names_the_uses_entry(self, tmp_path):
        raml, occurrences, uri = _parsed(tmp_path)
        link = raml.fragments[uri].uses['lib']
        found = {(o.role, o.span.line) for o in occurrences.of(link.id)}
        assert found == {
            (Role.DEFINITION, _where(API, 'lib:')[0]),
            (Role.ALIAS_PREFIX, _where(API, 'lib.Person')[0]),
            (Role.ALIAS_PREFIX, _where(API, 'lib.audited')[0]),
        }

    def test_a_link_names_the_fragment_it_decoded_to(self, tmp_path):
        raml, occurrences, uri = _parsed(tmp_path)
        money = raml.fragments[uri.rsplit('/', 1)[0] + '/money.raml']
        link = _only(occurrences.at(uri, *_where(API, 'money.raml')))
        assert link.target == money.id

    def test_a_link_to_a_file_that_is_no_fragment_has_no_target(self, tmp_path):
        _, occurrences, uri = _parsed(tmp_path)
        link = _only(occurrences.at(uri, *_where(API, 'readme.md')))
        assert (link.role, link.target) == (Role.LINK, None)

    def test_a_dotted_scheme_name_is_one_token(self, tmp_path):
        # `basic.v1` names no library: only the declared name splits a prefix off.
        _, occurrences, uri = _parsed(tmp_path)
        line, column = _where(API, 'basic.v1]')
        found = [o for o in occurrences.in_file(uri) if o.span.line == line]
        assert [(o.role, o.written, o.span.column) for o in found] == [(Role.REFERENCE, 'basic.v1', column)]


class TestTemplates:
    def test_a_name_in_a_template_applied_twice_is_one_occurrence(self, tmp_path):
        _, occurrences, uri = _parsed(tmp_path)
        assert _only(occurrences.at(uri, *_where(API, 'User\n'))).role is Role.REFERENCE

    def test_a_substituted_name_fails_the_law_and_is_dropped(self, tmp_path):
        # The substituted scalar keeps the template's position, where
        # `<<item>>` is written, not `User` (docs/11 § 3).
        api = API.replace('type: User\n', 'type: <<item>>\n').replace(
            'type: collection\n', 'type: {collection: {item: User}}\n'
        )
        _, occurrences, uri = _parsed(tmp_path, api)
        dropped = _only([o for o in occurrences.dropped if o.written == 'User'])
        assert (dropped.role, dropped.uri, dropped.span.line) == (Role.REFERENCE, uri, _where(api, '<<item>>')[0])
        assert not occurrences.at(uri, *_where(api, '<<item>>'))


class TestHitTest:
    def test_a_cursor_inside_the_name_finds_it(self, tmp_path):
        _, occurrences, uri = _parsed(tmp_path)
        line, column = _where(API, 'Person')
        assert _only(occurrences.at(uri, line, column + 3)).written == 'Person'

    @pytest.mark.parametrize(
        ('needle', 'shift'),
        [
            pytest.param('Person', len('Person'), id='just past the end'),
            pytest.param('.Person', 0, id='on the dot'),
            pytest.param('friend?', len('friend'), id='on the question mark'),
        ],
    )
    def test_a_cursor_outside_every_name_finds_nothing(self, tmp_path, needle, shift):
        _, occurrences, uri = _parsed(tmp_path)
        line, column = _where(API, needle)
        assert occurrences.at(uri, line, column + shift) == []

    def test_a_file_with_no_occurrences_finds_nothing(self, tmp_path):
        _, occurrences, _ = _parsed(tmp_path)
        assert occurrences.at('file:///elsewhere.raml', 1, 1) == []


class TestTheModelItReads:
    def test_it_needs_the_retained_source(self, tmp_path):
        raml = parse_from_path(_write(tmp_path), ParseOptions())
        with pytest.raises(ValueError, match='retain_source'):
            build_occurrences(raml)

    def test_a_lenient_model_gives_what_its_stages_bound(self, tmp_path):
        # P4 stops at the unknown trait, before P7 reads a type expression.
        entry = _write(tmp_path, API.replace('is: [paged,', 'is: [nope, paged,'))
        raml, error = parse_lenient(entry, ParseOptions(retain_source=True))
        assert error is not None
        found = _found(build_occurrences(raml), path_to_file_uri(entry))
        assert (Role.DEFINITION, Kind.TYPE, 'User', *_where(API, 'User:')) in found
        assert not any(role is Role.REFERENCE and kind is Kind.TYPE for role, kind, *_ in found)
