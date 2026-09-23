"""Turn RAML names into Python names.

**An address identifies a type; a name does not.** Two files may both declare
`Page`, and nothing reports the collision (docs/16 § 2). So every generated
name is claimed through a `Names` registry keyed by address: the first claimant
of a spelling keeps it, and the next one is given an alternative.

Claims are made in declaration order, so the name a type gets depends on the
document rather than on the order some walk happened to reach it.
"""

from __future__ import annotations

import keyword
import re
import urllib.parse

__all__ = ['Names', 'class_name', 'field_name', 'module_name']

_NON_WORD = re.compile(r'[^0-9A-Za-z]+')
_BOUNDARY = re.compile(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')

#: Builtins worth not shadowing. `id` and `type` matter most: both are ordinary
#: RAML property names, so a document hits them often.
_SHADOWED = frozenset({'id', 'type', 'filter', 'format', 'hash', 'input', 'list', 'next', 'object', 'property'})


def _words(text: str) -> list[str]:
    """`priceHistory` and `price-history` both become `['price', 'history']`."""
    return [part for chunk in _NON_WORD.split(text) for part in _BOUNDARY.split(chunk) if part]


def class_name(text: str) -> str:
    """`price-history` -> `PriceHistory`."""
    parts = _words(text)
    if not parts:
        return 'Unnamed'
    name = ''.join(part[:1].upper() + part[1:] for part in parts)
    return f'Type{name}' if name[0].isdigit() else name


def module_name(text: str) -> str:
    """`PriceHistory` -> `price_history`, never a keyword."""
    parts = [part.lower() for part in _words(text)]
    if not parts:
        return 'unnamed'
    name = '_'.join(parts)
    if name[0].isdigit():
        name = f'_{name}'
    return f'{name}_' if keyword.iskeyword(name) else name


def field_name(text: str) -> str:
    """A property name as an attribute, with keywords and shadows suffixed.

    A RAML property may be spelled anything at all, so this is not reversible;
    the generated `to_dict` carries the wire name and this is only how Python
    refers to it.
    """
    name = module_name(text)
    if keyword.iskeyword(name) or name in _SHADOWED or name.startswith('__'):
        return f'{name}_'
    return name


def from_address(address: str) -> str:
    """Name a node that has no name of its own.

    An anonymous body or nested object still has an address, and the address is
    the only thing that separates two of them. Use the tail of the address
    rather than a hash of it, so the name says where the type came from:
    `.../get/returns/200/payload/x` becomes `GetReturns200PayloadX`.
    """
    fragment = urllib.parse.unquote(address.partition('#')[2] or address)
    parts = [part for part in fragment.split('/') if part and part not in {'schema', 'declarations', 'web-api'}]
    return class_name('-'.join(parts[-4:])) if parts else 'Unnamed'


class Names:
    """Generated names, claimed by address and never handed out twice."""

    __slots__ = ('_by_address', '_taken')

    def __init__(self, reserved: frozenset[str] = frozenset()) -> None:
        self._by_address: dict[str, str] = {}
        self._taken: set[str] = set(reserved)

    def claim(self, address: str, preferred: str, *alternatives: str) -> str:
        """Return the name for `address`, which never changes once given.

        Alternatives are tried in order before a number is appended. A caller
        that knows a better way to tell two `Page` types apart than `Page2` —
        the file each was declared in — can supply it that way.
        """
        existing = self._by_address.get(address)
        if existing is not None:
            return existing
        name = self._free(preferred, alternatives)
        self._by_address[address] = name
        self._taken.add(name)
        return name

    def get(self, address: str) -> str | None:
        return self._by_address.get(address)

    def _free(self, preferred: str, alternatives: tuple[str, ...]) -> str:
        for candidate in (preferred, *alternatives):
            if candidate not in self._taken:
                return candidate
        for suffix in range(2, 1000):
            candidate = f'{preferred}{suffix}'
            if candidate not in self._taken:
                return candidate
        raise RuntimeError(f'a thousand declarations named {preferred}')
