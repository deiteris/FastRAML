"""The decorators: what makes a handler validated, secured, or hidden.

`validate` reads the signature and the return annotation once, compiles them,
and wraps the handler so every request is checked before it runs. What it leaves
on the wrapper is what `render.py` reads, so the document and the checking come
from one reading.

    @validate
    async def listing(title: str | None = None) -> Annotated[
        web.Response, Responds(200, list[Book])
    ]: ...

    @validate.and_request     # the handler wants the request as its first argument
    @validate.in_method       # the handler is a view method; `self` is skipped
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import update_wrapper
from inspect import signature
from textwrap import dedent
from typing import TYPE_CHECKING, Any, Final, get_type_hints

from aiohttp_raml.injectors import bind
from aiohttp_raml.multipart import PartRejected
from aiohttp_raml.params import read_signature
from aiohttp_raml.responses import read_responses
from aiohttp_raml.security import AUTH_SCHEMES, AuthenticationError, AuthorizationError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aiohttp import web

    from aiohttp_raml.injectors import Bound

__all__ = ['DESCRIBED', 'EXCLUDE', 'Described', 'exclude', 'secured', 'validate']

#: The attribute `validate` leaves on a wrapper, and `render.py` reads.
DESCRIBED: Final = 'aiohttp_raml_described'
#: The attribute `exclude` sets.
EXCLUDE: Final = 'aiohttp_raml_exclude'


@dataclass(slots=True)
class Described:
    """Everything this package knows about one handler."""

    bound: Bound
    #: `securedBy:` as alternatives, each a scheme name and its scopes.
    secured_by: list[tuple[str, list[str]]] = field(default_factory=list)
    description: str | None = None
    display_name: str | None = None


def exclude(target: Any) -> Any:
    """Mark a handler, a view or a resource as no part of the described API.

    aiohttp has no `include_in_schema`, and an app that describes itself serves
    the routes that do the describing. Returns its argument, so it decorates.
    """
    setattr(target, EXCLUDE, True)
    return target


def excluded(target: Any) -> bool:
    return bool(getattr(target, EXCLUDE, False))


def described(target: Any) -> Described | None:
    found = getattr(target, DESCRIBED, None)
    return found if isinstance(found, Described) else None


def secured(scheme: str, *, scopes: Sequence[str] = ()) -> Any:
    """Require `scheme` for this handler.

    Stacks: each application is one entry of `securedBy:`, which RAML reads as
    alternatives. There is no `and`, because RAML has no spelling for one.
    """

    def decorate(handler: Any) -> Any:
        pending = getattr(handler, '_aiohttp_raml_secured', [])
        # Prepended: decorators apply bottom-up, and the document should list
        # them the way they are written.
        handler._aiohttp_raml_secured = [(scheme, [*scopes]), *pending]  # noqa: SLF001
        entry = described(handler)
        if entry is not None:
            entry.secured_by = handler._aiohttp_raml_secured  # noqa: SLF001
        return handler

    return decorate


@dataclass(slots=True)
class _Validate:
    """`validate`, and its two variants."""

    #: Skip the first parameter, which is `self`.
    method: bool = False
    #: Pass the request through as the handler's first argument.
    with_request: bool = False
    #: Check each response against what the handler declared.
    check_responses: bool = False

    @property
    def in_method(self) -> _Validate:
        return _Validate(method=True, check_responses=self.check_responses)

    @property
    def and_request(self) -> _Validate:
        return _Validate(with_request=True, check_responses=self.check_responses)

    def __call__(self, handler: Any = None, /, *, check_responses: bool = False) -> Any:
        if handler is None:
            return _Validate(self.method, self.with_request, check_responses)
        return self._wrap(handler)

    def _wrap(self, handler: Any) -> Any:
        hints = get_type_hints(handler, include_extras=True)
        first = next(iter(signature(handler).parameters), '')
        ignore: tuple[str, ...] = ()
        if self.method and first == 'self':
            ignore = ('self',)
        elif self.with_request and first:
            ignore = (first,)

        display_name, description = _docstring(handler)
        entry = Described(
            bound=bind(read_signature(handler, hints, ignore), read_responses(handler, hints)),
            secured_by=list(getattr(handler, '_aiohttp_raml_secured', [])),
            description=description,
            display_name=display_name,
        )
        wrapper = self._wrapper(handler, entry)
        update_wrapper(wrapper, handler)
        setattr(wrapper, DESCRIBED, entry)
        return wrapper

    def _wrapper(self, handler: Any, entry: Described) -> Any:
        check = self.check_responses
        with_request = self.with_request

        async def run(request: web.Request, call: Any, first: Any) -> Any:
            """Authorise, validate, call, check. The whole of one request.

            `PartRejected` is caught here rather than in the injector because a
            *file* can only break a facet once the handler is reading it, which
            is after the injector has handed control over.
            """
            await _authorise(request, entry)
            try:
                ran, response = await entry.bound.call(call, request, first)
            except PartRejected as refused:
                return refused.response()
            if check and ran:
                entry.bound.check(response, handler)
            return response

        if self.method:

            async def in_method(view: Any) -> Any:
                return await run(view.request, lambda *args, **kwargs: handler(view, *args, **kwargs), None)

            return in_method

        async def plain(request: web.Request) -> Any:
            return await run(request, handler, request if with_request else None)

        return plain


async def _authorise(request: web.Request, entry: Described) -> None:
    """Try each alternative in turn; the first that authenticates decides.

    `securedBy:` is a list of alternatives, so one success is enough and the
    last failure is what the caller is told about.
    """
    if not entry.secured_by:
        return
    registry = request.app.get(AUTH_SCHEMES) or {}
    failure: Exception | None = None
    for name, scopes in entry.secured_by:
        scheme = registry.get(name)
        if scheme is None:
            raise RuntimeError(f'securedBy names {name!r}, which security.setup() did not register')
        try:
            identity = await scheme.authenticate(request)
        except AuthenticationError as error:
            failure = error
            continue
        if not await scheme.permits(request, identity, scopes):
            failure = AuthorizationError(f'{name}: {sorted(scopes)}')
            continue
        request['identity'] = identity
        return
    raise failure if failure is not None else AuthenticationError('no scheme accepted the request')


def _docstring(handler: Any) -> tuple[str | None, str | None]:
    """A handler's docstring as `(displayName, description)`.

    **A one-line docstring is the description.** Most one-liners are prose a
    developer wrote to say what the handler does, not a label they chose for it,
    and `displayName` replaces the method's name wherever RAML shows one --
    which is a claim about naming that a passing comment should not make.

    A docstring with a summary line *and* a body is the case where the author
    separated the two themselves, so PEP 257's own division applies: the summary
    becomes `displayName` and the body becomes `description`.

    There is no block convention to strip out of either.
    """
    doc = getattr(handler, '__doc__', None)
    if not doc:
        return None, None
    summary, _, rest = doc.strip().partition('\n')
    body = dedent(rest).strip()
    if not body:
        return None, summary.strip() or None
    return summary.strip() or None, body


validate: Final = _Validate()
