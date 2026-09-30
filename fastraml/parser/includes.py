"""`!include` resolution, its cache, and its limits.

`!include` is a YAML tag on a scalar, so the composer leaves it alone and this
module decides what it means. Two entry points exist and choosing between them
matters:

* `note_include_ref` records the directive and resolves its URI without reading
  anything. Use it when a *fragment* parser will load the target through its own
  cache — an `!include` of a DataType, a Trait, a NamedExample.
* `resolve_include` reads the target and splices its content into the current
  tree. Use it only for data.

Calling `resolve_include` where `note_include_ref` suffices doubles the I/O for
every typed-fragment include. See docs/03-yaml-and-io.md § 4.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from fastraml.errors import ErrorKind, RamlError
from fastraml.uris import path_to_file_uri, resolve_uri_ref
from fastraml.yamlnode import TAG_INCLUDE, TAG_STR, Node, NodeKind, compose, decode_source, node_error, read_head

if TYPE_CHECKING:
    from fastraml.parser.fragments import LibraryLink, ReferenceResolver
    from fastraml.parser.resourcetypes import ResourceTypeDefinition
    from fastraml.parser.security import SecuritySchemeDefinition
    from fastraml.parser.traits import TraitDefinition
    from fastraml.positions import Position
    from fastraml.registry import Raml
    from fastraml.types.base import BaseShape

__all__ = [
    'IncludeInfo',
    'IncludeRef',
    'IncludedContent',
    'content_anchor',
    'content_include',
    'inline_include',
    'note_include_ref',
    'resolve_include',
    'resolve_include_uri',
    'resolve_ref_uri',
    'strip_uri_suffix',
]

#: How every RAML document's first line begins: `#%RAML 1.0 Trait`, `#%RAML 0.8`.
RAML_HEADER_PREFIX: Final = '#%RAML'

_RAML_HEADER_BYTES: Final = RAML_HEADER_PREFIX.encode()
_UTF8_BOM: Final = b'\xef\xbb\xbf'

#: Skip quoted strings, including escaped quotes, when normalizing JSON whitespace.
_JSON_STRINGS_OR_TABS: Final = re.compile(r'"(?:[^"\\]|\\[\s\S])*"|\t+')

#: Include arguments composed as YAML. Everything else becomes a string scalar,
#: which is how `content: !include legal.md` works (spec section Includes).
_YAML_EXTENSIONS: Final = frozenset({'.raml', '.yaml', '.yml', '.json'})


@dataclass(frozen=True, slots=True)
class IncludeInfo:
    """The `!include` directive that provided one value."""

    #: The literal argument, as written.
    path: str
    #: The resolved absolute URI of the target.
    abs_uri: str


@dataclass(frozen=True, slots=True)
class IncludeRef:
    """One resolved `!include`, recorded in `Raml.include_refs` for tooling.

    Nothing in the parser reads these; an editor integration can emit document
    links from them.
    """

    source_uri: str
    path: str
    abs_uri: str
    position: Position


#: What opens a template variable. Spec section Resource Type and Trait
#: Parameters: "Parameters cannot be used within any file location that is used
#: in the context of modularization, that is, any file location defined in the
#: `!include` tag or as a value of any of the `uses` or `extends` nodes."
#:
#: The opening marker alone, not `parse_template_variables`: the rule is about
#: a parameter being present, not well-formed, and `<<` without `>>` is not a
#: file name anyone meant either.
_PARAMETER_OPENS: Final = '<<'


def resolve_ref_uri(raml: Raml, ref: str, location: str, position: Position | None = None) -> str:
    """Resolve a RAML path reference against `location`, or the workspace root.

    A reference beginning with `/` is *RAML-absolute*: the spec resolves it
    against the workspace root, not the filesystem root, which is what makes
    such a path portable. Everything else is ordinary RFC 3986 resolution
    against the referring file. Used for `!include` arguments and for `uses:`
    values, which have the same three argument forms — and which are also the
    two places the spec forbids a template parameter, so the check belongs here
    rather than at either call site.

    Includes resolve before templates expand, so without this check
    `!include <<version>>.raml` would reach the loader as a literal file name:
    reported as missing, or accepted if such a file happened to exist.
    """
    if _PARAMETER_OPENS in ref:
        raise RamlError.new(
            'path must not contain a template parameter',
            location,
            position,
            kind=ErrorKind.PARSING,
            info={'path': ref},
        )
    root = raml.workspace_root_uri
    if ref.startswith('/') and root:
        base = root if root.endswith('/') else root + '/'
        # The leading slash is stripped so that the workspace root is treated as
        # a base directory rather than being replaced by an absolute path.
        return resolve_uri_ref(base, ref[1:])
    return resolve_uri_ref(path_to_file_uri(location), ref)


def resolve_include_uri(raml: Raml, node: Node, location: str) -> str:
    """The absolute URI an `!include` node points at.

    The single authoritative URI computation for every include dispatch site.
    """
    return resolve_ref_uri(raml, node.value, location, node.position)


def _append_include_ref(raml: Raml, node: Node, location: str) -> str:
    abs_uri = resolve_include_uri(raml, node, location)
    raml.include_refs.setdefault(location, []).append(
        IncludeRef(source_uri=location, path=node.value, abs_uri=abs_uri, position=node.position)
    )
    return abs_uri


def note_include_ref(raml: Raml, node: Node, location: str) -> str:
    """Record an `!include` and return its URI, without reading the target.

    Returns `''` for a node that is not an include, so a caller can branch on
    the result.
    """
    if node.tag != TAG_INCLUDE:
        return ''
    return _append_include_ref(raml, node, raml.document_location(node, location))


def resolve_include(raml: Raml, node: Node, location: str) -> tuple[str, Node]:
    """Read an `!include` target and return `(target_uri, content)`.

    A node that is not an include is returned unchanged with an empty URI, so
    every value-decoding site can call this unconditionally.

    The composed content is cached per parse: a target referenced from five
    places is read and composed once. The caller must keep the *original* node
    for positions and provenance, and use the returned node only for the value.
    """
    if node.tag != TAG_INCLUDE:
        return '', node

    # Relative to the file that wrote the `!include`, which in a target tree
    # need not be the document being decoded (docs/19 § 5.3).
    location = raml.document_location(node, location)
    try:
        target = _append_include_ref(raml, node, location)
    except ValueError as err:  # a malformed location or argument
        raise RamlError.wrap('include: resolve URI', err, location, node.full_position) from err

    cached = raml.include_nodes.get(target)
    if cached is None:
        data = raml.include_data.pop(target, None)
        if data is None:
            data = _load(raml, node, target, location)
        try:
            cached = raml.include_nodes[target] = _compose_include(raml, node, data, target)
        except UnicodeDecodeError as err:
            raise node_error('include is not UTF-8', location, node, info={'path': target}) from err
    head = raml.include_heads.get(target)
    if head is not None:
        # A typed fragment is a declaration of its kind, which has a place of
        # its own; where data or content goes, only a file without one reads as
        # written (docs/03 § 4.2).
        raise node_error('fragment is not allowed here', location, node, info={'path': target, 'header': head})
    return target, cached


def inline_include(raml: Raml, node: Node, location: str) -> tuple[Node, str]:
    """What a position that takes a mapping or a sequence reads for `node`.

    An `!include` of a file without a RAML header stands for its content,
    located in that file, as if written in place (docs/03 § 4.2); a typed
    fragment is `fragment is not allowed here`. Anything else, and a file that
    is not YAML, is returned as written, for the position to judge.
    """
    if node.tag != TAG_INCLUDE or not _composes_as_yaml(node.value):
        return node, location
    target, content = resolve_include(raml, node, location)
    return content, target


def content_include(raml: Raml, node: Node, location: str, *, schema: bool = False) -> tuple[Node, str] | None:
    """For a position that takes a typed fragment: the content an `!include` of
    a file without a RAML header stands for, and that file.

    `None` leaves the node to the fragment path: a file with a header, one that
    is not YAML, one that cannot be read (the fragment path reports it), and,
    where `schema`, a `.json` file, which is a JSON Schema there. Content is read
    as if written in place, its names resolving where it is included
    (docs/03 § 4.2).
    """
    if node.tag != TAG_INCLUDE or not _composes_as_yaml(node.value):
        return None
    if schema and strip_uri_suffix(node.value).lower().endswith('.json'):
        return None
    try:
        target = resolve_include_uri(raml, node, raml.document_location(node, location))
    except ValueError:
        return None
    if raml.get_fragment(target) is not None or _has_raml_header(raml, target) is not False:
        return None
    target, content = resolve_include(raml, node, location)
    return content, target


def _has_raml_header(raml: Raml, target: str) -> bool | None:
    """Whether the file begins `#%RAML`; `None` if it cannot be read.

    Reads at most the include limit, keeping a complete read for the reader
    that follows; a larger typed fragment is read again by its own loader.
    """
    if target in raml.include_nodes:
        return target in raml.include_heads
    limit = raml.max_include_size
    try:
        data = raml.loader.load(target, max_bytes=limit if limit > 0 else None)
    except OSError:
        return None
    if not 0 < limit < len(data):
        raml.include_data[target] = data
    return data.removeprefix(_UTF8_BOM).startswith(_RAML_HEADER_BYTES)


def _composes_as_yaml(ref: str) -> bool:
    return posixpath.splitext(strip_uri_suffix(ref))[1].lower() in _YAML_EXTENSIONS


def _load(raml: Raml, node: Node, target: str, location: str) -> bytes:
    limit = raml.max_include_size
    try:
        data = raml.loader.load(target, max_bytes=limit if limit > 0 else None)
    except OSError as err:
        raise RamlError.wrap(
            'include', err, location, node.full_position, kind=ErrorKind.LOADING, info={'path': target}
        ) from err
    # The loader was asked for limit + 1 bytes, so an oversized file is detected
    # without ever being read whole.
    if 0 < limit < len(data):
        raise node_error('include file exceeds size limit', location, node, info={'path': target, 'limit': limit})
    return data


def _compose_include(raml: Raml, node: Node, data: bytes, target: str) -> Node:
    text = decode_source(data)
    if _composes_as_yaml(node.value):
        head = read_head(text)
        if head.startswith(RAML_HEADER_PREFIX):
            raml.include_heads[target] = head
        if strip_uri_suffix(node.value).lower().endswith('.json'):
            text = _json_tabs_as_spaces(text)
        return compose(text, uri=target, max_depth=raml.max_depth, key_pool=raml.mapping_keys)
    # Spec section Resolving Includes: any other file is included as a scalar.
    return Node(NodeKind.SCALAR, TAG_STR, text)


def _json_tabs_as_spaces(text: str) -> str:
    """Normalize JSON whitespace for both YAML scanners, preserving positions.

    The pure-Python scanner rejects tabs even inside flow collections. Replace
    each tab outside a quoted string with one space; string contents, including
    escaped quotes and tabs, must stay untouched. Tab-free files need no copy.
    """
    if '\t' not in text:
        return text
    return _JSON_STRINGS_OR_TABS.sub(_json_space, text)


def _json_space(match: re.Match[str]) -> str:
    token = match.group()
    return ' ' * len(token) if token[0] == '\t' else token


def strip_uri_suffix(ref: str) -> str:
    """Drop a `#fragment` or `?query` before taking a file extension.

    `schemas/order.json#/definitions/Item` is a JSON include, not an include of
    something with the extension `.json#`.
    """
    for separator in ('#', '?'):
        index = ref.find(separator)
        if index >= 0:
            ref = ref[:index]
    return ref


class IncludedContent:
    """The namespace of content included literally: its includer's, located in its own file.

    A trait or resource type written in a file without a RAML header resolves
    its names where it is included, not in a namespace of its own, which only
    a typed fragment has (docs/03 § 4.2). But its body is grafted under its
    anchor, and a grafted node is located by its anchor (docs/08 § 4.2), so the
    includer itself would name the wrong file. This resolves as the includer
    does and is located where the content is written.
    """

    __slots__ = ('host', 'id', 'kind', 'location', 'uses')

    def __init__(self, host: ReferenceResolver, location: str) -> None:
        # Always the fragment itself: content included from content resolves
        # in the one namespace at the top.
        self.host: ReferenceResolver = host.host if isinstance(host, IncludedContent) else host
        self.id = self.host.id
        self.kind = self.host.kind
        self.uses = self.host.uses
        self.location = location

    def __repr__(self) -> str:
        return f'IncludedContent({self.location!r} in {self.host.location!r})'

    def reference_type(self, name: str) -> BaseShape:
        return self.host.reference_type(name)

    def reference_annotation_type(self, name: str) -> BaseShape:
        return self.host.reference_annotation_type(name)

    def resource_type_definition(self, name: str) -> ResourceTypeDefinition:
        return self.host.resource_type_definition(name)

    def trait_definition(self, name: str) -> TraitDefinition:
        return self.host.trait_definition(name)

    def security_scheme_definition(self, name: str) -> SecuritySchemeDefinition:
        return self.host.security_scheme_definition(name)

    def library_link(self, prefix: str) -> LibraryLink | None:
        return self.host.library_link(prefix)


def content_anchor(anchor: ReferenceResolver | None, location: str) -> ReferenceResolver | None:
    """`anchor`, or an `IncludedContent` of it when what is decoded at
    `location` was written in another file than the namespace it resolves in.
    """
    if anchor is None or anchor.location == location:
        return anchor
    return IncludedContent(anchor, location)
