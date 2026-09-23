"""Overlays and Extensions: load the `extends` chain, merge it, decode the result.

The entry document names its master in `extends`; the master may itself be an
Overlay or Extension, down to one API, the root API (docs/19 § 2). Each file is
composed once. The chain is then applied from the root API outward with
`merge_extension`, and the resulting target tree is decoded **once** as the
root API's `APIFragment`, so neither the root API nor any extension document
body is decoded on its own (invariant I3).

Every node the merge takes from an extension document is marked as that
document's (docs/19 § 5.3), which is how a name, an `!include` or a diagnostic
inside it is read in the right file.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Final

from fastraml.domains import DomainLocation
from fastraml.errors import Accumulator, ErrorKind, RamlError
from fastraml.parser.extension_merge import merge_extension
from fastraml.parser.facets import make_string_facet
from fastraml.parser.fragments import (
    FRAGMENT_TARGETS,
    APIFragment,
    ExtensionFragment,
    FragmentKind,
    LibraryLink,
    ReferenceResolver,
    identify_fragment,
    load_fragment_text,
    resolve_uses,
    unmarshal_uses,
)
from fastraml.parser.includes import resolve_ref_uri
from fastraml.registry import ParseCtx
from fastraml.yamlnode import NodeKind, compose, is_null, node_error, pairs, read_head

if TYPE_CHECKING:
    from fastraml.registry import Raml
    from fastraml.yamlnode import Node

__all__ = ['EXTENSION_KINDS', 'decode_extension_chain']

#: The fragment kinds that carry `extends`.
EXTENSION_KINDS: Final = frozenset({FragmentKind.OVERLAY, FragmentKind.EXTENSION})
#: What an `extends` may name.
_MASTER_KINDS: Final = frozenset({FragmentKind.API, *EXTENSION_KINDS})


@dataclass(slots=True, eq=False)
class _Document:
    uri: str
    kind: FragmentKind
    root: Node
    #: The master's URI; empty for the root API.
    extends: str = ''


def decode_extension_chain(raml: Raml, uri: str, kind: FragmentKind, text: str) -> APIFragment:
    """Decode the entry Overlay or Extension at `uri` into its target tree's API.

    `raml.entry_point` is the API from before the merge, so `parse_lenient`
    returns the API even when the merge or the decode fails.
    """
    chain = _load(raml, uri, kind, text, seen=[uri])
    root_api = chain[-1]
    documents = chain[-2::-1]

    api = APIFragment(raml, root_api.uri)
    api.kind = FragmentKind.API
    raml.entry_point = _register(raml, api)

    accumulator = Accumulator()
    if not any(key.value == 'title' for key, _ in pairs(root_api.root)):
        # The target tree takes its title from here (docs/19 § 2).
        accumulator.add(node_error('title is required', root_api.uri, root_api.root))

    target = root_api.root
    declared_by: dict[str, dict[str, int]] = {}
    fragments: list[ExtensionFragment] = []
    for position, document in enumerate(documents, start=1):
        fragment = ExtensionFragment(raml, document.uri)
        fragment.kind = document.kind
        fragment.position = position
        fragment.extends = document.extends
        fragment.api = api
        fragments.append(_register(raml, fragment))
        _decode_own_keys(raml, fragment, document, accumulator)
        result = merge_extension(
            target,
            document.root,
            location=document.uri,
            overlay=document.kind is FragmentKind.OVERLAY,
            mark=partial(raml.mark_authored, author=fragment),
        )
        if result.error is not None:
            accumulator.add(result.error)
        fragment.removed_properties = result.removed
        for name, key in result.changed_annotation_types.items():
            raml.annotation_type_changes[name] = (document.uri, key.position)
        for declaration_kind, names in result.declared.items():
            origins = declared_by.setdefault(declaration_kind, {})
            for name in names:
                origins.setdefault(name, position)
        target = result.tree

    raml.extensions = fragments
    # Before the decode: a root `securedBy:` binds its schemes while decoding.
    api.declared_by = declared_by

    raml.push_ctx(ParseCtx(anchor=api, target=DomainLocation.API))
    try:
        api.decode(target)
    except RamlError as err:
        accumulator.add(err)
    finally:
        raml.pop_ctx()

    _resolve_libraries(raml, api, fragments, accumulator)
    accumulator.raise_if_any()
    return api


# -- loading the chain --------------------------------------------------------


def _load(raml: Raml, uri: str, kind: FragmentKind, text: str, *, seen: list[str]) -> list[_Document]:
    """The chain from `uri` to the root API, entry first.

    Recursive, so a failure anywhere is wrapped once per document on the way
    back out: the outermost frame is always the entry's `extends`.
    """
    raml.store_source_text(uri, text)
    root = compose(text, uri=uri, max_depth=raml.max_depth)
    raml.store_source_node(uri, root)
    if root.kind is not NodeKind.MAPPING:
        raise node_error('must be map', uri, root)
    if kind is FragmentKind.API:
        return [_Document(uri, kind, root)]

    extends = _extends_node(uri, root)
    try:
        master = resolve_ref_uri(raml, extends.value, uri, extends.position)
        if master in seen:
            raise RamlError.new('extends cycle', master, info={'chain': ' -> '.join([*seen, master])})
        master_text = load_fragment_text(raml, master)
        head = read_head(master_text)
        master_kind = identify_fragment(head)
        if master_kind is None:
            raise RamlError.new('unknown fragment kind', master, info={'head': head}, kind=ErrorKind.PARSING)
        if master_kind not in _MASTER_KINDS:
            raise RamlError.new(
                'unexpected fragment kind',
                master,
                info={'expected': 'API, Overlay or Extension', 'found': str(master_kind)},
                kind=ErrorKind.PARSING,
            )
        chain = _load(raml, master, master_kind, master_text, seen=[*seen, master])
    except RamlError as err:
        raise RamlError.wrap('resolve extends', err, uri, extends.full_position) from err
    return [_Document(uri, kind, root, master), *chain]


def _extends_node(uri: str, root: Node) -> Node:
    for key, value in pairs(root):
        if key.value == 'extends':
            if value.kind is not NodeKind.SCALAR or is_null(value) or not value.value:
                raise node_error('extends must be a string', uri, value)
            return value
    raise node_error('extends is required', uri, root)


# -- applying it --------------------------------------------------------------


def _register[F: ReferenceResolver](raml: Raml, fragment: F) -> F:
    """Index a chain document by its URI, as a fragment and as a namespace."""
    raml.put_fragment(fragment.location, fragment)
    raml.put_resolver(fragment.location, fragment)
    return fragment


def _decode_own_keys(raml: Raml, fragment: ExtensionFragment, document: _Document, accumulator: Accumulator) -> None:
    """`usage` and `uses`, the two keys the merge ignores (docs/19 § 2)."""
    raml.push_ctx(ParseCtx(anchor=fragment, target=FRAGMENT_TARGETS[document.kind]))
    try:
        for key, value in pairs(document.root):
            try:
                if key.value == 'usage':
                    fragment.usage = make_string_facet(raml, key, value, fragment.location)
                elif key.value == 'uses':
                    fragment.uses = unmarshal_uses(raml, value, fragment.location)
            except RamlError as err:
                accumulator.add(err)
    finally:
        raml.pop_ctx()


def _resolve_libraries(
    raml: Raml, api: APIFragment, fragments: list[ExtensionFragment], accumulator: Accumulator
) -> None:
    """Resolve every document's `uses:`, and combine each with its masters' (docs/19 § 5.2)."""
    try:
        resolve_uses(raml, api.uses, api.location)
    except RamlError as err:
        accumulator.add(err)
    visible: dict[str, LibraryLink] = dict(api.uses)
    for fragment in fragments:
        try:
            resolve_uses(raml, fragment.uses, fragment.location)
        except RamlError as err:
            accumulator.add(err)
        combined = dict(visible)
        for prefix, link in fragment.uses.items():
            earlier = visible.get(prefix)
            if earlier is not None:
                mine, theirs = _library_uri(raml, link), _library_uri(raml, earlier)
                if mine != theirs:
                    accumulator.add(
                        RamlError.new(
                            'library namespace conflict',
                            fragment.location,
                            link.key_pos,
                            info={'library': prefix, 'uri': mine, 'other': theirs},
                        )
                    )
                    continue
            combined[prefix] = link
        fragment.visible_uses = visible = combined


def _library_uri(raml: Raml, link: LibraryLink) -> str:
    if link.link is not None:
        return link.link.location
    try:
        return resolve_ref_uri(raml, link.value, link.location, link.value_pos)
    except RamlError:
        return link.value
