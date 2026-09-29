"""Which routes an app describes, and the `(verb, handler)` pairs each one stands for."""

from __future__ import annotations

import importlib
import re
from typing import TYPE_CHECKING, Any, Final

from aiohttp import hdrs

from aiohttp_raml.decorator import excluded

if TYPE_CHECKING:
    from collections.abc import Iterator

    from raml_document.from_pydantic import Walk

__all__ = ['apps', 'crosses_segments', 'operations', 'resources', 'segment_patterns']

#: The applications a resource is mounted under, outermost first: the app
#: itself, then each sub-application down to the one that routes it.
Chain = tuple[Any, ...]


def apps(app: Any, chain: Chain = ()) -> Iterator[Chain]:
    """`app` and every sub-application mounted under it, each as the chain that reaches it."""
    chain = (*chain, app)
    yield chain
    for entry in app.router.resources():
        info = entry.get_info()
        if not excluded(entry) and 'prefix' in info and 'app' in info:
            yield from apps(info['app'], chain)


def resources(app: Any, walk: Walk, chain: Chain = ()) -> Iterator[tuple[Chain, str, Any]]:
    """Every resource the app routes to -- a sub-application's among them -- with its path.

    `add_subapp` prefixes each of the sub-application's resources as it mounts
    them, so their paths are already whole. A sub-application matched by the
    request's host (`add_domain`) is not under the document's one `baseUri`,
    and a resource with no path -- a static directory -- is no API operation;
    both are reported.
    """
    chain = (*chain, app)
    for entry in app.router.resources():
        if excluded(entry):
            continue
        info = entry.get_info()
        if 'app' in info:
            if 'prefix' in info:
                yield from resources(info['app'], walk, chain)
            else:
                walk.drop(str(entry), 'a sub-application matched by host, and RAML has one baseUri; not described')
            continue
        path = info.get('path') or info.get('formatter')
        if path is None:
            walk.drop(str(entry), 'a resource with no path is not an API operation; not described')
            continue
        yield chain, path, entry


#: The regex aiohttp gives a `{name}` segment that states none.
SEGMENT: Final = r'[^{}/]+'
#: Where a segment's group starts in a resource's compiled pattern.
_GROUP: Final = re.compile(r'\(\?P<(\w+)>')
#: The regex parser, which the standard library keeps private: it is what
#: says whether a regex can match a `/`. Untyped, and read only here.
_sre: Any = importlib.import_module('re._parser')
_ops: Any = importlib.import_module('re._constants')
_SLASH: Final = ord('/')


def segment_patterns(resource: Any) -> dict[str, str]:
    """Each URI parameter's own regex -- `{isbn:[0-9]{13}}` -> `isbn: [0-9]{13}` -- where the route states one.

    Read off the resource's compiled pattern, which aiohttp builds from one
    `(?P<name>regex)` group per parameter: the group's text runs to its
    balancing parenthesis, stepping over escapes and character classes.
    """
    pattern = resource.get_info().get('pattern')
    if pattern is None:
        return {}
    text: str = pattern.pattern
    out: dict[str, str] = {}
    for match in _GROUP.finditer(text):
        regex = _group(text, match.end())
        if regex != SEGMENT:
            out[match.group(1)] = regex
    return out


def _group(text: str, start: int) -> str:
    """The text of the group opened just before `start`, up to its closing parenthesis."""
    depth, index, in_class = 1, start, False
    while index < len(text):
        char = text[index]
        if char == '\\':
            index += 2
            continue
        if in_class:
            in_class = char != ']'
        elif char == '[':
            in_class = True
            # A `]` first in a class, or after its `^`, is a member.
            if text[index + 1 : index + 2] == '^':
                index += 1
            if text[index + 1 : index + 2] == ']':
                index += 1
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if not depth:
                return text[start:index]
        index += 1
    return text[start:]


def crosses_segments(regex: str) -> bool:
    """Can `regex` match a `/`, and so a value spanning several path segments?"""
    return _admits_slash(_sre.parse(regex))


def _admits_slash(tree: Any) -> bool:
    """Does any one-character matcher in a parsed regex match `/`? Lookarounds consume nothing."""
    return any(_node_admits_slash(op, value) for op, value in tree)


def _node_admits_slash(op: Any, value: Any) -> bool:  # noqa: PLR0911 - one return per opcode
    if op is _ops.ANY:
        return True
    if op is _ops.LITERAL:
        return bool(value == _SLASH)
    if op is _ops.NOT_LITERAL:
        return bool(value != _SLASH)
    if op is _ops.IN:
        return _class_admits_slash(value)
    if op in (_ops.MAX_REPEAT, _ops.MIN_REPEAT, _ops.POSSESSIVE_REPEAT):
        return _admits_slash(value[2])
    if op is _ops.SUBPATTERN:
        return _admits_slash(value[3])
    if op is _ops.ATOMIC_GROUP:
        return _admits_slash(value)
    if op is _ops.BRANCH:
        return any(_admits_slash(branch) for branch in value[1])
    return False


#: The classes `/` belongs to: it is no digit, no space and no word character.
_SLASH_CATEGORIES: Final = frozenset({'CATEGORY_NOT_DIGIT', 'CATEGORY_NOT_SPACE', 'CATEGORY_NOT_WORD'})


def _class_admits_slash(items: Any) -> bool:
    negated = bool(items) and items[0][0] is _ops.NEGATE
    member = False
    for op, value in items:
        if (
            (op is _ops.LITERAL and value == _SLASH)
            or (op is _ops.RANGE and value[0] <= _SLASH <= value[1])
            or (op is _ops.CATEGORY and str(value) in _SLASH_CATEGORIES)
        ):
            member = True
    return member != negated


def operations(resource: Any, walk: Walk, path: str) -> list[tuple[str, Any]]:
    """The `(verb, handler)` pairs one resource describes, leaving out what `exclude` marks.

    `add_get` also routes HEAD to the GET handler, which is what HTTP says a
    HEAD is -- a GET without its body -- so that HEAD says nothing a reader of
    `get:` does not already know, and would describe a body a HEAD never has.
    A HEAD with a handler of its own is described.
    """
    routes = [route for route in resource if not excluded(route.handler)]
    gets = {route.handler for route in routes if route.method == hdrs.METH_GET}
    out: list[tuple[str, Any]] = []
    for route in routes:
        if route.method == hdrs.METH_HEAD and route.handler in gets:
            continue
        out.extend(pair for pair in _verbs(route, walk, path) if not excluded(pair[1]))
    return out


def _verbs(route: Any, walk: Walk, path: str) -> list[tuple[str, Any]]:
    """The `(verb, handler)` pairs one route describes.

    A class-based route carries the method `*`, and the verbs it answers are the
    ones the class defines. A *function* registered for `*` names no verbs at
    all, and RAML has no wildcard method -- `*:` is not a node a parser accepts
    -- so there is nothing to write and the route is reported instead.
    """
    handler = route.handler
    if not isinstance(handler, type):
        if route.method == hdrs.METH_ANY:
            walk.drop(path, 'a handler registered for every method names none, and RAML has no wildcard method')
            return []
        return [(route.method, handler)]
    if route.method != hdrs.METH_ANY:
        return [(route.method, getattr(handler, route.method.lower()))]
    verbs = sorted(verb for verb in hdrs.METH_ALL if hasattr(handler, verb.lower()))
    if not verbs:
        walk.drop(path, f'{handler.__name__} defines no HTTP method')
    return [(verb, getattr(handler, verb.lower())) for verb in verbs]
