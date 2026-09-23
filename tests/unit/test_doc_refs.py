"""References into `docs/` name a document and a heading that exist.

A comment or docstring cites a section as `docs/NN § S` or
`docs/NN-name.md § S`, where `S` is the number a heading starts with: `4`,
`3.1`, `B4`, or `A` for `## Part A`. The reference may break across comment
lines. Any other spelling of the section (`section 6`, `Part 4`) is rejected so
the check cannot be bypassed. Links between numbered documents
(`05-type-model.md#6-discriminators`) are checked against GitHub heading anchors.

`docs/archive/` and `docs/research/` are history and are not scanned; neither
is this file, whose cases are deliberately broken.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DOCS = _ROOT / 'docs'
_SUFFIXES = frozenset({'.py', '.pyi', '.ts', '.tsx', '.go', '.md', '.toml', '.j2', '.yml', '.yaml', '.json'})
_SKIPPED = ('docs/archive/', 'docs/research/', 'tests/unit/test_doc_refs.py')

_HEADING = re.compile(r'^#{1,6}\s+(?:Part\s+)?([0-9A-Z][0-9A-Za-z.]*?)\.?(?:\s|$)', re.MULTILINE)
_ANY_HEADING = re.compile(r'^#{1,6}\s+(.+?)\s*$', re.MULTILINE)
#: What may separate a document from its `§`: whitespace, comment markers,
#: a closing backtick or parenthesis.
_GAP = r'[\s#/*`)]*'
_REF = re.compile(
    rf'docs/(?P<num>\d\d)(?P<slug>-[a-z0-9-]+\.md)?(?:{_GAP}§\s*(?P<sec>[0-9A-Z][0-9A-Za-z.]*[0-9A-Za-z]|[0-9A-Z]))?'
)
_NONCANONICAL = re.compile(rf'docs/\d\d(?:-[a-z0-9-]+\.md)?`?{_GAP}(?:[Ss]ections?|[Pp]arts?)\s+[0-9A-Z]')
_MD_LINK = re.compile(r'\]\((?P<file>\d\d-[a-z0-9-]+\.md)(?:#(?P<anchor>[^)\s]+))?\)')


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r'[^\w\- ]', '', heading.strip().lower())
    return text.replace(' ', '-')


def _documents() -> dict[str, tuple[str, set[str], set[str]]]:
    """Document number -> (file name, section numbers, heading anchors)."""
    out: dict[str, tuple[str, set[str], set[str]]] = {}
    for path in sorted(_DOCS.glob('[0-9][0-9]-*.md')):
        text = path.read_text(encoding='utf-8')
        sections = set(_HEADING.findall(text))
        anchors = {_slug(h) for h in _ANY_HEADING.findall(text)}
        out[path.name[:2]] = (path.name, sections, anchors)
    return out


def _tracked() -> list[str]:
    git = shutil.which('git')
    if git is None:
        pytest.skip('git is needed to list tracked files')
    listed = subprocess.run([git, 'ls-files'], cwd=_ROOT, capture_output=True, text=True, check=True)  # noqa: S603 - fixed arguments
    return [
        name
        for name in listed.stdout.splitlines()
        if pathlib.PurePosixPath(name).suffix in _SUFFIXES
        and not name.startswith(_SKIPPED)
        and (_ROOT / name).is_file()
    ]


def _line(text: str, offset: int) -> int:
    return text.count('\n', 0, offset) + 1


def _broken(name: str, text: str, docs: dict[str, tuple[str, set[str], set[str]]]) -> list[str]:
    found: list[str] = []
    for match in _REF.finditer(text):
        num, slug, sec = match.group('num', 'slug', 'sec')
        where = f'{name}:{_line(text, match.start())}'
        if num not in docs:
            found.append(f'{where}: no document docs/{num}')
            continue
        file_name, sections, _ = docs[num]
        if slug is not None and f'{num}{slug}' != file_name:
            found.append(f'{where}: docs/{num}{slug} is named {file_name}')
        if sec is not None and sec not in sections:
            found.append(f'{where}: {file_name} has no section {sec}')
    found.extend(
        f'{name}:{_line(text, match.start())}: spell the section as `§`: {match.group()!r}'
        for match in _NONCANONICAL.finditer(text)
    )
    if name.startswith('docs/'):
        by_name = {file_name: anchors for file_name, _, anchors in docs.values()}
        for match in _MD_LINK.finditer(text):
            target, anchor = match.group('file', 'anchor')
            where = f'{name}:{_line(text, match.start())}'
            if target not in by_name:
                found.append(f'{where}: no document {target}')
            elif anchor is not None and anchor not in by_name[target]:
                found.append(f'{where}: {target} has no anchor #{anchor}')
    return found


def test_every_docs_reference_resolves() -> None:
    docs = _documents()
    broken = [
        problem for name in _tracked() for problem in _broken(name, (_ROOT / name).read_text('utf-8', 'replace'), docs)
    ]
    assert not broken, '\n'.join(broken)


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('see docs/07 § 4', 0),
        ('see docs/07-resolution-and-inheritance.md § 4.', 0),
        ('see (`docs/09` § B4)', 0),
        ('# see docs/09-security-and-annotations.md\n# § A', 0),
        ('see docs/07 § 9', 1),
        ('see docs/99', 1),
        ('see docs/07-wrong-name.md', 1),
        ('see docs/07 section 4', 1),
        ('see docs/09-security-and-annotations.md sections A1 and A3', 1),
        ('see `docs/12` Part 4', 1),
    ],
)
def test_the_checker_reads_references(text: str, expected: int) -> None:
    assert len(_broken('x.py', text, _documents())) == expected


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('[x](05-type-model.md#6-discriminators)', 0),
        ('[x](01-scope-and-coverage.md#41-numeric-formats)', 0),
        ('[x](05-type-model.md#7-nothing)', 1),
        ('[x](05-no-such.md)', 1),
    ],
)
def test_the_checker_reads_document_links(text: str, expected: int) -> None:
    assert len(_broken('docs/x.md', text, _documents())) == expected
