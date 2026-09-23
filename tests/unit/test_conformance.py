"""The conformance corpus, answered in all three languages — docs/16 § 7.

One set of questions, one set of expected answers, and a driver per language
that reads the first and prints its own. This file does the comparing; the
drivers hold no expectations.

What it exists to catch is the failure the per-backend suites structurally
cannot: three readings of the traversal contract (docs/16 § 6.1) that are each
locally correct and not the same rule, such as a link recognised as "sole key
is `$ref`" in one language and "no `type` key" in another.

Go's driver needs a toolchain and TypeScript's needs the viewer's
`node_modules`, so both skip where those are absent. CI's `bindings` job
installs both and asserts this file reports no skips: a skip here is the
cross-language check not running, which is the whole of what the corpus is for.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from fastraml import ParseOptions, parse_from_path
from fastraml.views.bindings import golang, python, typescript
from fastraml.views.bindings.conformance import CORPUS, SOURCES, build_corpus, load_corpus
from fastraml.views.bindings.golang import golang_conform, golang_runtime
from fastraml.views.bindings.python import python_conform, python_runtime
from fastraml.views.bindings.typescript import typescript_conform, typescript_runtime
from fastraml.views.tree import build_tree

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
VIEWER = ROOT / 'viewer'
#: The worked document the corpus's second tree is projected from.
SAMPLE_SOURCE = 'fixtures/sample/api.raml'
SAMPLE_ROOT = 'fixtures'
REGENERATE = 'python -m fastraml.views.bindings.conformance'
ORDERED_MAP = 'github.com/wk8/go-ordered-map/v2@v2.1.8'


@pytest.fixture(scope='module')
def expected():
    return load_corpus()


def _run(*arguments: str, cwd: pathlib.Path) -> str:
    result = subprocess.run(  # noqa: S603 - executable and arguments are test-owned
        arguments, capture_output=True, text=True, cwd=cwd, check=False
    )
    if result.returncode != 0:
        pytest.fail(f'{arguments[0]} failed:\n{result.stdout[-2000:]}\n{result.stderr[-4000:]}')
    return result.stdout


def _compare(answered: dict, expected: dict) -> None:
    """Diff one driver's answers against the corpus, one question at a time.

    Question by question rather than whole: a walk that drifts reports 185
    addresses against 184, and the useful part of that is which one and where.
    """
    assert answered['refuse'] == [True] * len(expected['refuse']), 'an envelope this reader should refuse was accepted'
    assert set(answered['cases']) == set(expected['cases'])
    for name, case in expected['cases'].items():
        got = answered['cases'][name]
        for question, want in case['expected'].items():
            assert got[question] == want, f'{name}: {question}'


class TestPythonAnswersTheCorpus:
    @pytest.fixture(scope='class')
    @staticmethod
    def answered(tmp_path_factory):
        root = tmp_path_factory.mktemp('python-conform')
        package = root / 'vendored'
        package.mkdir()
        (package / '__init__.py').write_text('', encoding='utf-8')
        (package / 'tree.py').write_text(python(), encoding='utf-8')
        (package / 'walk.py').write_text(python_runtime(), encoding='utf-8')
        (package / 'conform.py').write_text(python_conform(), encoding='utf-8')
        return json.loads(_run(sys.executable, '-m', 'vendored.conform', str(CORPUS), cwd=root))

    def test_it_agrees_with_the_corpus(self, answered, expected):
        _compare(answered, expected)


@pytest.mark.skipif(shutil.which('go') is None, reason='no Go toolchain')
class TestGoAnswersTheCorpus:
    @pytest.fixture(scope='class')
    @staticmethod
    def answered(tmp_path_factory):
        root = tmp_path_factory.mktemp('go-conform')
        (root / 'tree').mkdir()
        (root / 'conform').mkdir()
        (root / 'tree' / 'tree.go').write_text(golang(), encoding='utf-8', newline='')
        (root / 'tree' / 'walk.go').write_text(golang_runtime(), encoding='utf-8', newline='')
        (root / 'conform' / 'main.go').write_text(golang_conform('conformance/tree'), encoding='utf-8', newline='')
        (root / 'go.mod').write_text('module conformance\n\ngo 1.24\n', encoding='utf-8', newline='')
        _run('go', 'mod', 'edit', f'-require={ORDERED_MAP}', cwd=root)
        _run('go', 'mod', 'tidy', cwd=root)
        return json.loads(_run('go', 'run', './conform', str(CORPUS), cwd=root))

    def test_it_agrees_with_the_corpus(self, answered, expected):
        _compare(answered, expected)

    def test_the_reading_half_is_gofmt_clean(self, tmp_path):
        """Its equivalent of ruff, and the reason it is not ruff's job."""
        (tmp_path / 'walk.go').write_text(golang_runtime(), encoding='utf-8', newline='')
        (tmp_path / 'conform.go').write_text(golang_conform(), encoding='utf-8', newline='')
        assert _run('gofmt', '-l', '.', cwd=tmp_path).strip() == ''


@pytest.mark.skipif(not (VIEWER / 'node_modules').is_dir(), reason='the viewer is not installed')
class TestTypeScriptAnswersTheCorpus:
    @pytest.fixture(scope='class')
    @staticmethod
    def answered(tmp_path_factory):
        # Run from the viewer, which is where `tsx` and a `tsconfig` live. The
        # files themselves go elsewhere, so what is measured is the generated
        # artifact rather than the viewer's copy of it.
        root = tmp_path_factory.mktemp('ts-conform')
        (root / 'tree.d.ts').write_text(typescript(), encoding='utf-8')
        (root / 'walk.ts').write_text(typescript_runtime(), encoding='utf-8')
        (root / 'conform.ts').write_text(typescript_conform(), encoding='utf-8')
        tsx = VIEWER / 'node_modules' / '.bin' / ('tsx.cmd' if sys.platform == 'win32' else 'tsx')
        return json.loads(_run(str(tsx), str(root / 'conform.ts'), str(CORPUS), cwd=VIEWER))

    def test_it_agrees_with_the_corpus(self, answered, expected):
        _compare(answered, expected)


class TestTheCorpusIsNotStale:
    """Both halves: the trees, and the answers built from them.

    `cases.json` is regenerated from the trees, so checking only that leaves the
    trees themselves with no source of truth -- a change to the projection would
    move the committed tree's *source* and the corpus would go on measuring the
    old shape. Each tree is therefore regenerated from the RAML it came from.
    """

    def test_the_every_kind_tree_is_current(self, workspace):
        source = SOURCES.joinpath('every-kind.raml').read_text(encoding='utf-8')
        root = workspace({'api.raml': source})
        projected = build_tree(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
        current = json.loads((CORPUS / 'trees' / 'every-kind.json').read_text(encoding='utf-8'))
        assert current == projected, f'regenerate the corpus trees -- every-kind.json is stale ({REGENERATE})'

    def test_the_bookstore_tree_is_current(self):
        raml = parse_from_path(ROOT / SAMPLE_SOURCE, ParseOptions(unwrap=True, workspace_root=ROOT / SAMPLE_ROOT))
        current = json.loads((CORPUS / 'trees' / 'bookstore.json').read_text(encoding='utf-8'))
        assert current == build_tree(raml), f'regenerate the corpus trees -- bookstore.json is stale ({REGENERATE})'

    def test_regenerating_the_answers_changes_nothing(self):
        current = json.loads((CORPUS / 'cases.json').read_text(encoding='utf-8'))
        assert current == build_corpus(), f'cases.json is stale ({REGENERATE})'

    def test_every_tree_in_the_corpus_has_a_case(self):
        trees = {path.stem for path in (CORPUS / 'trees').glob('*.json')}
        assert trees == set(load_corpus()['cases'])

    def test_the_questions_reach_all_three_constructs(self):
        """A corpus that only ever met containment would pass on a broken stop.

        Links and markers are both rare in a small document and both are where a
        reading goes wrong, so the probe list is checked for reaching each.
        """
        labels = [
            label
            for case in load_corpus()['cases'].values()
            for children in case['expected']['children'].values()
            for label in children
        ]
        assert any(label.startswith('ref:') for label in labels)
        assert any(label.startswith('recursive:') for label in labels)
        assert any(label.startswith('shape:') for label in labels)
