"""What each Python type is in RAML, and how a RAML type name is spelled -- stated once."""

from __future__ import annotations

import collections
import collections.abc
import datetime
import decimal
import ipaddress
import pathlib
import re
import uuid
from typing import Any, Final

from pydantic import AnyUrl, EmailStr, NameEmail, SecretBytes, SecretStr

__all__ = ['ANY_KEY', 'KEY_PATTERNS', 'MAPPINGS', 'SCALARS', 'SEQUENCES', 'SETS', 'type_name']

#: A Python type -> the RAML built-in that carries it.
SCALARS: Final[dict[Any, str]] = {
    str: 'string',
    int: 'integer',
    float: 'number',
    decimal.Decimal: 'number',
    bool: 'boolean',
    bytes: 'file',
    datetime.datetime: 'datetime',
    datetime.date: 'date-only',
    datetime.time: 'time-only',
    uuid.UUID: 'string',
    type(None): 'nil',
    Any: 'any',
    # What pydantic writes as text. A duration is ISO 8601 -- `PT5S` -- by
    # default; `ser_json_timedelta='float'` would make it a number.
    datetime.timedelta: 'string',
    pathlib.PurePath: 'string',
    ipaddress.IPv4Address: 'string',
    ipaddress.IPv6Address: 'string',
    ipaddress.IPv4Network: 'string',
    ipaddress.IPv6Network: 'string',
    ipaddress.IPv4Interface: 'string',
    ipaddress.IPv6Interface: 'string',
    AnyUrl: 'string',
    EmailStr: 'string',
    NameEmail: 'string',
    SecretStr: 'string',
    SecretBytes: 'string',
}

#: The containers read as an array, the ones of them whose items are unique,
#: and the ones read as a map -- concrete or abstract, bare or parametrised.
SETS: Final = frozenset({set, frozenset, collections.abc.Set, collections.abc.MutableSet})
SEQUENCES: Final = SETS | {
    list,
    tuple,
    collections.deque,
    collections.abc.Sequence,
    collections.abc.MutableSequence,
    collections.abc.Collection,
    collections.abc.Iterable,
}
MAPPINGS: Final = frozenset(
    {dict, collections.OrderedDict, collections.defaultdict, collections.abc.Mapping, collections.abc.MutableMapping}
)

#: Where a `dict` key type narrows RAML's pattern-any property.
#:
#: The `/…/` delimiters are what make a property name a *pattern* rather than a
#: literal key, and RAML matches one unanchored, so each is anchored by hand.
#: `//` is the bare pattern-any: an empty regex between the delimiters.
ANY_KEY: Final = '//'
KEY_PATTERNS: Final[dict[Any, str]] = {
    int: r'/^[-+]?\d+$/',
    uuid.UUID: r'/^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/',
}

#: What a RAML type name may not contain. `Page[Book]`, the `__name__` pydantic
#: gives a parametrised generic, is a type *expression* to a RAML parser -- an
#: array suffix it then fails to read -- so the brackets become underscores, the
#: spelling pydantic's own JSON Schema uses for the same class.
_NOT_NAME: Final = re.compile(r'[^A-Za-z0-9_]')


def type_name(text: str) -> str:
    """`text` as a name RAML reads as a name: `Page[Book]` is `Page_Book_`."""
    return _NOT_NAME.sub('_', text)
