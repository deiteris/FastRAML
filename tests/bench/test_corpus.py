"""The generated corpora have to be valid RAML, in all four configurations.

This runs in the ordinary suite, at a scale small enough to be free, because a
benchmark corpus that only parses with `validate=False` measures the wrong
thing and says nothing while doing it. The first draft of `write_small` had a
required property its own example omitted; `parse` and `unwrap` were happy, and
the two configurations that matter for P10 were measuring an exception.
"""

from __future__ import annotations

import pytest

from bench import corpus
from pyraml import ParseOptions, parse_from_path

CONFIGURATIONS = [
    pytest.param(ParseOptions(), id='parse'),
    pytest.param(ParseOptions(unwrap=True), id='unwrap'),
    pytest.param(ParseOptions(validate=True), id='validate'),
    pytest.param(ParseOptions(unwrap=True, validate=True), id='unwrap+validate'),
]

WRITERS = {
    'small': lambda root: corpus.write_small(root, type_count=12),
    'large': lambda root: corpus.write_large(root, type_count=24, library_count=4),
    'endpoints': lambda root: corpus.write_endpoints(root, resource_count=3),
    'validate': lambda root: corpus.write_validate(root, type_count=3),
    'jsonschema': lambda root: corpus.write_jsonschema(root, schema_count=6, shared_count=2),
}


@pytest.mark.parametrize('name', sorted(WRITERS))
@pytest.mark.parametrize('options', CONFIGURATIONS)
def test_corpus_parses_cleanly(tmp_path, name, options):
    entry = WRITERS[name](tmp_path)
    raml = parse_from_path(entry, options)
    assert raml.entry_point is not None


def test_generation_is_deterministic(tmp_path):
    """A baseline describes an input, so the input has to be reproducible."""
    first = corpus.write_large(tmp_path / 'a', type_count=24, library_count=4)
    second = corpus.write_large(tmp_path / 'b', type_count=24, library_count=4)
    written = sorted(path.relative_to(first.parent) for path in first.parent.rglob('*') if path.is_file())
    assert written == sorted(path.relative_to(second.parent) for path in second.parent.rglob('*') if path.is_file())
    for name in written:
        assert (first.parent / name).read_bytes() == (second.parent / name).read_bytes()


def test_large_reaches_common_by_two_spellings(tmp_path):
    """The diamond `bench_large` exists to exercise (docs/12 section 2).

    Every library reaches one `common.raml` through a relative path spelt from
    its own directory. If the compose cache ever stops canonicalising, this
    corpus stops being a linearity check and starts being a quadratic one, so
    the property is pinned here rather than left to the benchmark to notice.
    """
    entry = corpus.write_large(tmp_path, type_count=24, library_count=4)
    raml = parse_from_path(entry)
    common = [uri for uri in raml.fragments if uri.endswith('/common.raml')]
    assert len(common) == 1
