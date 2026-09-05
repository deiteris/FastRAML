"""docs/14-testing.md section 4's laws, checked over the corpus.

`test_invariants.py` next door checks the *architectural* invariants of
docs/02 § 4 — what a `Raml` looks like after a successful parse. This file
checks the *behavioural* laws: run the parser twice, or two ways, and compare.

Several of these are listed in doc 14 § 4 as hypothesis properties and are
realised here against the TCK instead. That is deliberate. A generator writes
the documents someone thought to describe; the corpus contains the ones people
actually wrote, including the awkward ones nobody would generate. Where a law
genuinely needs generated input — the merge laws — it lives in
`tests/property/`.
"""

from __future__ import annotations

import os

import pytest

from tests.tck.conftest import collect_fixtures, fixture_id, tck_root

pytestmark = pytest.mark.tck


def _root_or_skip():
    root = tck_root()
    if root is None:
        pytest.skip('no TCK corpus; set PYRAML_TCK_DIR')
    return root


@pytest.fixture(scope='module')
def corpus() -> list:
    """Every valid fixture that parses, as `(id, Raml)` pairs.

    A fixture the parser rejects is skipped: these laws are about the shape of a
    successful parse, and the ratchet already tracks which fixtures fail.
    """
    from pyraml import ParseOptions, RamlError, parse_from_path

    root = _root_or_skip()
    parsed = []
    for path in collect_fixtures('valid'):
        try:
            parsed.append((fixture_id(root, path), parse_from_path(path, ParseOptions())))
        except (RamlError, OSError):
            continue
    return parsed


@pytest.fixture(scope='module')
def verdicts() -> list:
    """Each fixture's verdict under both validation configurations.

    Invalid fixtures are included deliberately: the two paths have to agree
    about a *rejection* as much as about an acceptance, and the rejecting one is
    where the private copy does its work.
    """
    from pyraml import ParseOptions, RamlError, parse_from_path

    root = _root_or_skip()

    def verdict(path, options):
        try:
            parse_from_path(path, options)
        except RamlError as err:
            return 'reject', {trace.message for chain in err.chains() for trace in chain}
        return 'accept', set()

    copied = ParseOptions(validate=True)
    flattened = ParseOptions(validate=True, unwrap=True)
    results = []
    for kind in ('valid', 'invalid'):
        for path in collect_fixtures(kind):
            try:
                results.append((fixture_id(root, path), verdict(path, copied), verdict(path, flattened)))
            except OSError:
                continue
    return results


class TestTheseChecksSeeSomething:
    """A corpus test that iterates nothing passes just as quietly as one that works.

    Every assertion in this file has the form "no offenders". That is only
    evidence if the loop ran, and if the comparison it makes is capable of
    coming out non-empty — so both are asserted here rather than assumed.
    """

    def test_the_corpus_is_populated(self, corpus: list):
        assert len(corpus) > 400
        assert sum(len(raml.shapes) for _name, raml in corpus) > 1000

    def test_both_verdicts_are_observed(self, verdicts: list):
        outcomes = {copy[0] for _name, copy, _flat in verdicts}
        assert outcomes == {'accept', 'reject'}
        assert len(verdicts) > 900

    def test_the_wrapper_difference_is_real_and_not_a_dead_branch(self, verdicts: list):
        """The `allowed` set exists because ten fixtures need it.

        If the symmetric difference were always empty — say the two paths
        stopped being compared at all — the exclusion would be dead code and the
        test above would pass vacuously. This pins that it is load-bearing.
        """
        differing = [name for name, copy, flat in verdicts if copy[1] ^ flat[1]]
        assert differing, 'no fixture exercises the two wrappers; the exclusion is dead'


class TestValidationPathsAgree:
    """`validate=True` with and without `unwrap=True` must reach one verdict.

    docs/13 § 2: without `unwrap`, P10 flattens a **private copy** of each
    declaration and validates that, so the caller's model keeps its `inherits`
    and its `link`. Two code paths, one specification.

    Nothing else in the suite compares them. Every other test picks one
    configuration and stays inside it, so a copy that diverged from the real
    thing would be invisible: each configuration would agree with itself.
    """

    def test_every_fixture_gets_the_same_verdict_either_way(self, verdicts: list):
        offenders = [
            f'{name}: copy={copy[0]} flattened={flat[0]}' for name, copy, flat in verdicts if copy[0] != flat[0]
        ]
        assert not offenders, '\n'.join(offenders[:20])

    def test_the_diagnostics_differ_only_in_which_pass_wrapped_them(self, verdicts: list):
        """The inner diagnostic must match; only the outer frame may differ.

        `_ensure_unwrapped` wraps a failure as `unwrap for validation`, and P9
        wraps the same failure as `unwrap shape`. That pair is expected and
        affects ten fixtures. Any *other* difference would mean the two paths
        disagree about the reason while agreeing about the verdict, which is the
        divergence this is really looking for.
        """
        allowed = {'unwrap for validation', 'unwrap shape'}
        offenders = [
            f'{name}: {sorted((copy[1] ^ flat[1]) - allowed)}'
            for name, copy, flat in verdicts
            if (copy[1] ^ flat[1]) - allowed
        ]
        assert not offenders, '\n'.join(offenders[:20])


class TestDeterminism:
    """Law 9. Two parses of one input produce the same model.

    The only check that would catch a pass reading iteration order off a set, or
    state surviving on a class attribute between parses.
    """

    def test_two_parses_agree(self):
        from pyraml import ParseOptions, RamlError, parse_from_path

        root = _root_or_skip()
        options = ParseOptions(unwrap=True)
        offenders: list[str] = []
        for path in collect_fixtures('valid'):
            try:
                first = parse_from_path(path, options)
                second = parse_from_path(path, options)
            except (RamlError, OSError):
                continue
            if _projection(first) != _projection(second):
                offenders.append(fixture_id(root, path))
        assert not offenders, '\n'.join(offenders[:20])


def _projection(raml) -> list:
    """Enough of the model to notice a reordering or a substitution."""
    return [
        (shape.name, shape.type, shape.location, type(shape.shape).__name__, shape.key_pos) for shape in raml.shapes
    ]


class TestI2AndI3:
    """Law 6. One compose per file, one decode per fragment.

    Checked as **canonicalisation**, which is the half that fails silently.
    `./a/../b.raml` and `b.raml` reaching two cache entries decodes the file
    twice and produces two shape identities for one declaration: the parse still
    succeeds and the model is quietly wrong. The counting-loader tests in
    `test_includes.py` cover the other half — that a cache hit is a hit.
    """

    def test_no_file_is_reachable_under_two_uris(self, corpus: list):
        from pyraml.uris import file_uri_to_path

        offenders: list[str] = []
        for name, raml in corpus:
            seen: dict[str, str] = {}
            for uri in [*raml.fragments, *raml.include_nodes]:
                if not uri.startswith('file://'):
                    continue
                try:
                    real = os.path.realpath(file_uri_to_path(uri.partition('#')[0])).lower()
                except (OSError, ValueError):
                    continue
                if real in seen and seen[real] != uri:
                    offenders.append(f'{name}: {seen[real]} and {uri} are one file')
                seen.setdefault(real, uri)
        assert not offenders, '\n'.join(offenders[:20])


class TestPositionSanity:
    """Law 8. Every position lies inside the file it names, and is ordered.

    Positions are not decoration — they are what lets the model back a linter or
    an LSP (docs/11 § 3) — and a merely *plausible* position is invisible to the
    rest of the suite, which asserts on a diagnostic's message and not on where
    it points.
    """

    def test_positions_are_1_based_and_inside_their_file(self, corpus: list):
        from pyraml.uris import file_uri_to_path

        lengths: dict[str, int | None] = {}

        def line_count(uri: str) -> int | None:
            if uri not in lengths:
                try:
                    with open(file_uri_to_path(uri), encoding='utf-8-sig') as handle:
                        lengths[uri] = sum(1 for _ in handle)
                except (OSError, ValueError):
                    lengths[uri] = None
            return lengths[uri]

        offenders: list[str] = []
        for name, raml in corpus:
            for shape in raml.shapes:
                if not shape.location.startswith('file://'):
                    continue
                total = line_count(shape.location)
                for label in ('key_pos', 'value_pos'):
                    pos = getattr(shape, label)
                    if pos is None:
                        continue
                    if pos.line < 1 or pos.column < 1:
                        offenders.append(f'{name}: {label} {pos} is not 1-based')
                    elif total is not None and pos.line > total:
                        offenders.append(f'{name}: {label} {pos} past {total} lines of {shape.location}')
        assert not offenders, '\n'.join(offenders[:20])

    def test_a_key_never_starts_after_its_own_value(self, corpus: list):
        offenders = [
            f'{name}: key={shape.key_pos} value={shape.value_pos} in {shape.location}'
            for name, raml in corpus
            for shape in raml.shapes
            if shape.key_pos is not None
            and shape.value_pos is not None
            and (shape.key_pos.line, shape.key_pos.column) > (shape.value_pos.line, shape.value_pos.column)
        ]
        assert not offenders, '\n'.join(offenders[:20])
