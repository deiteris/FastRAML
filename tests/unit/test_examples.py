"""`example:` and its two forms.

See docs/05-type-model.md § 5.
"""

from __future__ import annotations

import pytest

from fastraml import RamlError
from fastraml.registry import Raml
from fastraml.types.examples import examples_of, make_example
from fastraml.yamlnode import Node, compose, pairs

LOCATION = 'file:///a.raml'


def pair_of(text: str) -> tuple[Node, Node]:
    """The key and value nodes of a one-key document."""
    return next(iter(pairs(compose(text, uri=LOCATION))))


def example(text: str, name: str = ''):
    return make_example(Raml(), *pair_of(text), name, LOCATION)


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


class TestIncludedExample:
    """`example: !include e.yaml` reads as if the file's content were written
    there, so a `value:` in it is the wrapper, as it is inline.
    """

    API = '#%RAML 1.0\ntitle: T\ntypes:\n  T:\n    properties:\n      a: integer\n'

    def parse(self, workspace, example: str, content: str):
        from fastraml import ParseOptions

        root = workspace({'api.raml': self.API + f'    {example}: !include e.yaml\n', 'e.yaml': content})
        return workspace.parse(root / 'api.raml', ParseOptions(validate=True, unwrap=True))

    def test_an_included_wrapper_is_unwrapped(self, workspace):
        raml = self.parse(workspace, 'example', 'displayName: One\nvalue:\n  a: 1\n')
        (ex,) = examples_of(raml.types_in(raml.location)['T'])
        assert (ex.data.raw, ex.display_name.value, ex.data.location.rsplit('/', 1)[-1]) == ({'a': 1}, 'One', 'e.yaml')

    def test_an_included_wrapper_under_a_name_is_unwrapped(self, workspace):
        raml = self.parse(workspace, 'examples:\n      one', 'value:\n  a: 1\n')
        assert [ex.data.raw for ex in examples_of(raml.types_in(raml.location)['T'])] == [{'a': 1}]

    def test_an_included_wrappers_strict_is_kept(self, workspace):
        raml = self.parse(workspace, 'example', 'strict: false\nvalue:\n  a: x\n')
        assert [ex.strict.value for ex in examples_of(raml.types_in(raml.location)['T'])] == [False]

    def test_a_nonconforming_included_value_is_reported_in_its_file(self, workspace):
        # At `a`, which only an unwrapped `value:` has.
        with pytest.raises(RamlError) as caught:
            self.parse(workspace, 'example', 'value:\n  a: x\n')
        (chain,) = caught.value.chains()
        assert (chain[-1].info['path'], chain[-1].where().rsplit('/', 1)[-1]) == ('$.a', 'e.yaml:2:6')

    def test_a_named_example_fragment_is_not_one_example(self, workspace):
        # A NamedExample is a map of named examples, whose place is `examples:`.
        with pytest.raises(RamlError) as caught:
            self.parse(workspace, 'example', '#%RAML 1.0 NamedExample\nvalue:\n  a: 1\n')
        assert (caught.value.head.message, caught.value.head.info['header']) == (
            'fragment is not allowed here',
            '#%RAML 1.0 NamedExample',
        )


class TestIncludedNamedExamples:
    """`examples: !include e.raml` — the examples are on the fragment.

    `Examples._values` is empty in that form, so a consumer reading it directly
    sees no examples and validates none. `entries()` is the one accessor.
    """

    API = '#%RAML 1.0\ntitle: T\n'

    def parse(self, workspace, files):
        from fastraml import ParseOptions

        root = workspace(files)
        try:
            workspace.parse(root / 'api.raml', ParseOptions(validate=True, unwrap=True))
        except RamlError as err:
            return err
        return None

    def test_an_included_example_that_does_not_conform_is_reported(self, workspace):
        # Neither the entry nor its include exists on disk.
        assert not (workspace.root / 'api.raml').exists()
        error = self.parse(
            workspace,
            {
                'api.raml': self.API
                + 'types:\n  T:\n    properties:\n      a: integer\n    examples: !include e.raml\n',
                'e.raml': '#%RAML 1.0 NamedExample\nfirst:\n  a: not a number\n',
            },
        )
        assert not (workspace.root / 'e.raml').exists()
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
    def test_an_example_is_placed_at_its_key_and_its_value(self):
        # The key was the value's position, so an outline could not select a name.
        found = example('terse:\n  a: 1\n', 'terse')
        assert (found.key_pos.line, found.key_pos.column, found.key_pos.end_column) == (1, 1, 6)
        assert (found.value_pos.line, found.value_pos.column) == (2, 3)

    def test_each_example_takes_an_id_from_the_parse(self):
        raml = Raml()
        first = make_example(raml, *pair_of('example: 1\n'), 'a', LOCATION)
        second = make_example(raml, *pair_of('example: 2\n'), 'b', LOCATION)
        assert first.id != second.id
        assert (first.name, second.name) == ('a', 'b')


class TestExamplesOf:
    """`examples_of` is the one reading of `example` and `examples` (AGENTS.md: `entries()`, never `_values`)."""

    API = '#%RAML 1.0\ntitle: T\ntypes:\n  T:\n    type: integer\n'

    def declared(self, workspace, files: dict[str, str]):
        from fastraml import ParseOptions

        root = workspace(files)
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        return raml.types_in(raml.location)['T']

    def test_named_examples_come_in_declaration_order(self, workspace):
        shape = self.declared(workspace, {'api.raml': self.API + '    examples:\n      b: 2\n      a: 1\n'})
        assert [example.name for example in examples_of(shape)] == ['b', 'a']

    def test_the_single_example_is_yielded(self, workspace):
        shape = self.declared(workspace, {'api.raml': self.API + '    example: 7\n'})
        assert [example.data.raw for example in examples_of(shape)] == [7]

    def test_an_included_named_example_is_followed(self, workspace):
        shape = self.declared(
            workspace,
            {
                'api.raml': self.API + '    examples: !include e.raml\n',
                'e.raml': '#%RAML 1.0 NamedExample\nfirst: 3\n',
            },
        )
        assert shape.examples._values == {}, 'the case this guards: `_values` is empty'
        assert [example.data.raw for example in examples_of(shape)] == [3]

    def test_a_non_strict_example_is_the_callers_to_skip(self, workspace):
        shape = self.declared(workspace, {'api.raml': self.API + '    example:\n      value: 1\n      strict: false\n'})
        assert [example.strict.value for example in examples_of(shape)] == [False]
