"""File references written relative to the output (docs/20 § 7.2).

An `!include` argument or a `uses` value is resolved against the file that
wrote it, then spelled relative to the output's directory. An HTTP(S) target is
kept as a URL.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING, Final
from urllib.parse import unquote, urlsplit

from fastraml.errors import ErrorKind, RamlError
from fastraml.parser.includes import strip_uri_suffix
from fastraml.uris import is_file_uri
from fastraml.yamlnode import TAG_INCLUDE, Node, NodeKind, with_content, with_value

if TYPE_CHECKING:
    from fastraml.join.compare import IncludeReader

__all__ = ['Rebaser']

#: The drive of a Windows file URI path, `/C:`.
_DRIVE: Final = re.compile(r'^/[A-Za-z]:')


class Rebaser:
    """Spells targets relative to one output directory."""

    __slots__ = ('_directory',)

    def __init__(self, output_uri: str) -> None:
        #: The output directory's URI path, with a trailing slash.
        self._directory = posixpath.dirname(urlsplit(output_uri).path).rstrip('/') + '/'

    def relative(self, target: str, node: Node, file_uri: str) -> str:
        """`target` relative to the output directory; a URL unchanged."""
        if not is_file_uri(target):
            return target
        bare = strip_uri_suffix(target)
        suffix = target[len(bare) :]
        path = urlsplit(bare).path
        directory = self._directory
        drive, other_drive = _DRIVE.match(path), _DRIVE.match(directory)
        if (drive and drive.group().lower()) != (other_drive and other_drive.group().lower()):
            raise RamlError.new(
                'join path not relative',
                file_uri,
                node.full_position,
                kind=ErrorKind.PARSING,
                info={'path': unquote(path), 'output': unquote(directory)},
            )
        return unquote(posixpath.relpath(path, directory)) + suffix

    def rebase(self, node: Node, file_uri: str, reader: IncludeReader) -> Node:
        """`node` with every `!include` argument rewritten; unchanged nodes are shared."""
        if node.tag == TAG_INCLUDE:
            value = self.relative(reader.target(node, file_uri), node, file_uri)
            return node if value == node.value else with_value(node, value)
        if node.kind is NodeKind.SCALAR:
            return node
        content = [self.rebase(child, file_uri, reader) for child in node.content]
        if all(new is old for new, old in zip(content, node.content, strict=True)):
            return node
        return with_content(node, content)
