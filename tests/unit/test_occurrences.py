"""The occurrence index (docs/16 § 9): where each name is written, and what it names."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fastraml import ParseOptions
from fastraml.uris import path_to_file_uri
from fastraml.views.occurrences import Kind, Link, Role, build_occurrences

if TYPE_CHECKING:
    from pathlib import Path

    from fastraml.registry import Raml
    from fastraml.views.occurrences import Occurrence, Occurrences
    from tests.unit.conftest import MemoryWorkspace

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
    securedBy: [basic.v1: {}]
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


def _write(workspace: MemoryWorkspace, api: str = API) -> Path:
    files = {'lib.raml': LIBRARY, 'money.raml': MONEY, 'readme.md': 'Read me.', 'api.raml': api}
    return workspace(files) / 'api.raml'


def _parsed(workspace: MemoryWorkspace, api: str = API) -> tuple[Raml, Occurrences, str]:
    entry = _write(workspace, api)
    raml = workspace.parse(entry, ParseOptions(unwrap=True, retain_text=True))
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
            pytest.param((Role.BUILTIN, Kind.TYPE, 'string'), ('string', 0), id='built-in written alone'),
            pytest.param((Role.BUILTIN, Kind.TYPE, 'object'), ('object', 0), id='built-in under type:'),
            pytest.param((Role.REFERENCE, Kind.RESOURCE_TYPE, 'collection'), ('collection', 1), id='type:'),
            pytest.param((Role.REFERENCE, Kind.TRAIT, 'paged'), ('paged', 1), id='is:'),
            pytest.param((Role.ALIAS_PREFIX, Kind.LIBRARY, 'lib'), ('lib.audited', 0), id='qualified is: prefix'),
            pytest.param((Role.REFERENCE, Kind.TRAIT, 'audited'), ('audited', 0), id='qualified is:'),
            pytest.param((Role.REFERENCE, Kind.SECURITY_SCHEME, 'basic.v1'), ('basic.v1]', 0), id='securedBy:'),
            pytest.param(
                (Role.REFERENCE, Kind.SECURITY_SCHEME, 'basic.v1'), ('basic.v1: {}', 0), id='securedBy: with parameters'
            ),
            pytest.param((Role.REFERENCE, Kind.ANNOTATION_TYPE, 'note'), ('note)', 0), id='annotation'),
            pytest.param((Role.LINK, Kind.FILE, 'lib.raml'), ('lib.raml', 0), id='uses: value'),
            pytest.param((Role.LINK, Kind.FILE, 'money.raml'), ('money.raml', 0), id='!include'),
        ],
    )
    def test_the_name_is_found_where_it_is_written(self, workspace, expected, site):
        _, occurrences, uri = _parsed(workspace)
        assert (*expected, *_where(API, *site)) in _found(occurrences, uri)

    def test_nothing_is_dropped_from_a_document_the_law_can_read(self, workspace):
        _, occurrences, _ = _parsed(workspace)
        assert occurrences.dropped == ()

    def test_a_declaration_in_a_library_is_found_in_its_file(self, workspace):
        _, occurrences, uri = _parsed(workspace)
        library = uri.rsplit('/', 1)[0] + '/lib.raml'
        assert (Role.DEFINITION, Kind.TYPE, 'Person', *_where(LIBRARY, 'Person')) in _found(occurrences, library)


class TestTargets:
    def test_a_reference_meets_its_definition_on_the_target(self, workspace):
        raml, occurrences, uri = _parsed(workspace)
        person = raml.types_in(uri.rsplit('/', 1)[0] + '/lib.raml')['Person']
        roles = sorted(o.role for o in occurrences.of(person.id))
        assert roles == [Role.DEFINITION, Role.REFERENCE]

    def test_a_prefix_names_the_uses_entry(self, workspace):
        raml, occurrences, uri = _parsed(workspace)
        link = raml.fragments[uri].uses['lib']
        found = {(o.role, o.span.line) for o in occurrences.of(link.id)}
        assert found == {
            (Role.DEFINITION, _where(API, 'lib:')[0]),
            (Role.ALIAS_PREFIX, _where(API, 'lib.Person')[0]),
            (Role.ALIAS_PREFIX, _where(API, 'lib.audited')[0]),
        }

    def test_a_link_names_the_fragment_it_decoded_to(self, workspace):
        raml, occurrences, uri = _parsed(workspace)
        money = raml.fragments[uri.rsplit('/', 1)[0] + '/money.raml']
        link = _only(occurrences.at(uri, *_where(API, 'money.raml')))
        assert link.target == money.id

    def test_a_link_to_a_file_that_is_no_fragment_has_no_target(self, workspace):
        _, occurrences, uri = _parsed(workspace)
        link = _only(occurrences.at(uri, *_where(API, 'readme.md')))
        assert (link.role, link.target) == (Role.LINK, None)
        # docs/16 § 9: it still names the file its path resolved to.
        assert isinstance(link, Link)
        assert link.resolved == uri.rsplit('/', 1)[0] + '/readme.md'

    def test_a_dotted_scheme_name_is_one_token(self, workspace):
        # `basic.v1` names no library: only the declared name splits a prefix off.
        _, occurrences, uri = _parsed(workspace)
        line, column = _where(API, 'basic.v1]')
        found = [o for o in occurrences.in_file(uri) if o.span.line == line]
        assert [(o.role, o.written, o.span.column) for o in found] == [(Role.REFERENCE, 'basic.v1', column)]


QUOTED = """#%RAML 1.0
title: Demo
uses:
  "lib": "lib.raml"
types:
  "User":
    properties:
      "friend?": "lib.Person | string"
annotationTypes:
  "note": string
securitySchemes:
  "basic.v1":
    type: Basic Authentication
traits:
  'paged': {}
/users:
  is: ["paged"]
  securedBy: ['basic.v1']
  "(note)": hi
  get:
"""


class TestQuotedScalars:
    """docs/11 § 3: a name in a quoted scalar starts one column past the quote."""

    @pytest.mark.parametrize(
        ('role', 'written', 'needle'),
        [
            pytest.param(Role.DEFINITION, 'lib', 'lib":', id='uses entry'),
            pytest.param(Role.LINK, 'lib.raml', 'lib.raml', id='uses: value'),
            pytest.param(Role.DEFINITION, 'User', 'User', id='type'),
            pytest.param(Role.DEFINITION, 'friend', 'friend?', id='optional property'),
            pytest.param(Role.ALIAS_PREFIX, 'lib', 'lib.Person', id='prefix in an expression'),
            pytest.param(Role.REFERENCE, 'Person', 'Person', id='name in an expression'),
            pytest.param(Role.BUILTIN, 'string', 'string"', id='keyword in an expression'),
            pytest.param(Role.DEFINITION, 'paged', "paged'", id='single-quoted trait'),
            pytest.param(Role.REFERENCE, 'paged', 'paged"', id='is:'),
            pytest.param(Role.REFERENCE, 'basic.v1', "basic.v1'", id='securedBy:'),
            pytest.param(Role.REFERENCE, 'note', 'note)', id='annotation'),
        ],
    )
    def test_the_name_is_found_past_the_quote(self, workspace, role, written, needle):
        _, occurrences, uri = _parsed(workspace, QUOTED)
        found = {(o.role, o.written, o.line, o.column) for o in occurrences.in_file(uri)}
        assert (role, written, *_where(QUOTED, needle)) in found

    def test_nothing_is_dropped(self, workspace):
        _, occurrences, _ = _parsed(workspace, QUOTED)
        assert occurrences.dropped == ()


class TestTemplates:
    def test_a_name_in_a_template_applied_twice_is_one_occurrence(self, workspace):
        _, occurrences, uri = _parsed(workspace)
        assert _only(occurrences.at(uri, *_where(API, 'User\n'))).role is Role.REFERENCE

    def test_a_substituted_name_is_found_where_the_caller_wrote_it(self, workspace):
        # docs/08 § 5.1: the template's scalar reads `<<item>>`; `User` is
        # written in the application.
        api = API.replace('type: User\n', 'type: <<item>>[]\n').replace(
            '  type: collection\n', '  type: {collection: {item: User}}\n'
        )
        _, occurrences, uri = _parsed(workspace, api)
        found = _only(occurrences.at(uri, *_where(api, 'User}')))
        assert (found.role, found.written, found.target) == (
            Role.REFERENCE,
            'User',
            occurrences.at(uri, *_where(api, 'User:'))[0].target,
        )
        assert occurrences.dropped == ()

    @staticmethod
    def _annotated(key: str, value: str) -> str:
        return API.replace('    get:\n      responses:', f'    get:\n      ({key}): hi\n      responses:').replace(
            '  type: collection\n', f'  type: {{collection: {{tag: {value}}}}}\n'
        )

    def test_a_substituted_annotation_name_is_found_where_the_caller_wrote_it(self, workspace):
        api = self._annotated('<<tag>>', 'note')
        _, occurrences, uri = _parsed(workspace, api)
        found = _only(occurrences.at(uri, *_where(api, 'note}')))
        assert (found.role, found.kind, found.written) == (Role.REFERENCE, Kind.ANNOTATION_TYPE, 'note')
        assert occurrences.dropped == ()

    def test_an_annotation_name_only_partly_substituted_is_dropped(self, workspace):
        # `<<tag>>te` is written in two places, so neither is where `note` is.
        api = self._annotated('<<tag>>te', 'no')
        _, occurrences, uri = _parsed(workspace, api)
        dropped = _only([o for o in occurrences.dropped if o.written == 'note'])
        assert (dropped.uri, dropped.line) == (uri, _where(api, '<<tag>>te')[0])

    def test_a_transformed_name_is_written_nowhere_and_is_dropped(self, workspace):
        api = API.replace('type: User\n', 'type: <<item | !uppercamelcase>>\n').replace(
            '  type: collection\n', '  type: {collection: {item: user}}\n'
        )
        _, occurrences, uri = _parsed(workspace, api)
        dropped = _only([o for o in occurrences.dropped if o.written == 'User'])
        assert (dropped.role, dropped.uri, dropped.line) == (Role.REFERENCE, uri, _where(api, '<<item')[0])


class TestHitTest:
    def test_a_cursor_inside_the_name_finds_it(self, workspace):
        _, occurrences, uri = _parsed(workspace)
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
    def test_a_cursor_outside_every_name_finds_nothing(self, workspace, needle, shift):
        _, occurrences, uri = _parsed(workspace)
        line, column = _where(API, needle)
        assert occurrences.at(uri, line, column + shift) == []

    def test_a_file_with_no_occurrences_finds_nothing(self, workspace):
        _, occurrences, _ = _parsed(workspace)
        assert occurrences.at('file:///elsewhere.raml', 1, 1) == []


class TestTheModelItReads:
    def test_it_needs_the_retained_source(self, workspace):
        raml = workspace.parse(_write(workspace))
        with pytest.raises(ValueError, match='retain_text'):
            build_occurrences(raml)

    def test_a_lenient_model_gives_what_its_stages_bound(self, workspace):
        # P4 stops at the unknown trait, before P7 reads a type expression.
        entry = _write(workspace, API.replace('is: [paged,', 'is: [nope, paged,'))
        raml, error = workspace.lenient(entry, ParseOptions(retain_text=True))
        assert error is not None
        found = _found(build_occurrences(raml), path_to_file_uri(entry))
        assert (Role.DEFINITION, Kind.TYPE, 'User', *_where(API, 'User:')) in found
        assert not any(role is Role.REFERENCE and kind is Kind.TYPE for role, kind, *_ in found)
