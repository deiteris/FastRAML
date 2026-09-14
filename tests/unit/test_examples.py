"""`example:` and its two forms.

See docs/05-type-model.md section 6.
"""

from __future__ import annotations

import pytest

from fastraml import RamlError
from fastraml.registry import Raml
from fastraml.types.examples import make_example
from fastraml.yamlnode import Node, compose, pairs

LOCATION = 'file:///a.raml'


def value_of(text: str) -> Node:
    """The value node of a one-key document."""
    _key, value = next(iter(pairs(compose(text, uri=LOCATION))))
    return value


def example(text: str, name: str = ''):
    return make_example(Raml(), value_of(text), name, LOCATION)


class TestFormA:
    def test_a_mapping_without_a_value_key_is_the_example_itself(self):
        assert example('example:\n  name: Bob\n  age: 7\n').data.raw == {'name': 'Bob', 'age': 7}

    def test_a_scalar_is_the_example_itself(self):
        assert example('example: Bob\n').data.raw == 'Bob'

    def test_a_sequence_is_the_example_itself(self):
        assert example('example:\n  - 1\n  - 2\n').data.raw == [1, 2]

    def test_no_metadata_is_read(self):
        # `strict` here is a property of the example object, not a wrapper key.
        ex = example('example:\n  strict: false\n  name: Bob\n')
        assert ex.strict is None
        assert ex.data.raw == {'strict': False, 'name': 'Bob'}


class TestFormB:
    def test_a_value_key_selects_the_wrapper_form(self):
        ex = example('example:\n  value:\n    name: Bob\n  strict: false\n')
        assert ex.data.raw == {'name': 'Bob'}
        assert ex.strict.value is False

    def test_display_name_and_description_are_read(self):
        ex = example('example:\n  value: 1\n  displayName: First\n  description: An example.\n')
        assert (ex.display_name.value, ex.description.value) == ('First', 'An example.')

    def test_annotations_are_collected(self):
        ex = example('example:\n  value: 1\n  (pii): true\n')
        assert ex.annotations['pii'].value.raw is True

    def test_an_unknown_wrapper_key_is_rejected_where_it_is_written(self):
        with pytest.raises(RamlError) as caught:
            example('example:\n  value: 1\n  stric: false\n')
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'unknown field'
        assert trace.info == {'field': 'stric'}
        assert trace.position.line == 3


class TestTheAmbiguity:
    def test_a_type_with_its_own_value_property_is_read_as_the_wrapper_form(self):
        # This is the cost of the rule, and the spec's own example comments on
        # it: such a type must write form B explicitly, as here.
        ex = example('example:\n  value:\n    value: 1\n')
        assert ex.data.raw == {'value': 1}

    def test_a_value_key_anywhere_in_the_mapping_selects_form_b(self):
        ex = example('example:\n  strict: true\n  value: 1\n')
        assert (ex.data.raw, ex.strict.value) == (1, True)


class TestIncludedNamedExamples:
    """`examples: !include e.raml` — the examples are on the fragment.

    `Examples.values` is empty in that form, so a consumer reading it directly
    sees no examples and validates none. `entries()` is the one accessor.
    """

    API = '#%RAML 1.0\ntitle: T\n'

    def parse(self, workspace, files):
        from fastraml import ParseOptions, parse_from_path

        root = workspace(files)
        try:
            parse_from_path(root / 'api.raml', ParseOptions(validate=True, unwrap=True))
        except RamlError as err:
            return err
        return None

    def test_an_included_example_that_does_not_conform_is_reported(self, workspace):
        error = self.parse(
            workspace,
            {
                'api.raml': self.API
                + 'types:\n  T:\n    properties:\n      a: integer\n    examples: !include e.raml\n',
                'e.raml': '#%RAML 1.0 NamedExample\nfirst:\n  a: not a number\n',
            },
        )
        assert error is not None
        assert 'invalid example' in str(error)

    def test_a_conforming_included_example_passes(self, workspace):
        assert (
            self.parse(
                workspace,
                {
                    'api.raml': self.API
                    + 'types:\n  T:\n    properties:\n      a: integer\n    examples: !include e.raml\n',
                    'e.raml': '#%RAML 1.0 NamedExample\nfirst:\n  a: 3\n',
                },
            )
            is None
        )


class TestIdentity:
    def test_each_example_takes_an_id_from_the_parse(self):
        raml = Raml()
        first = make_example(raml, value_of('example: 1\n'), 'a', LOCATION)
        second = make_example(raml, value_of('example: 2\n'), 'b', LOCATION)
        assert first.id != second.id
        assert (first.name, second.name) == ('a', 'b')
