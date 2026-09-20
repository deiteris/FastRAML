"""A starting point for implementing Bookstore API.

**This file is yours.** It has no generated header because nothing regenerates
it: `raml-codegen` rewrites `bookstore_server/` around it and leaves this
alone unless asked with `--force`. Fill in the methods and delete this
docstring.

    uvicorn impl:app --reload

Every method raises `NotImplementedError` until you replace it.

## When the document changes

Regenerate in place. **No new method appears here** -- nothing writes to this
file again, so adding one is yours. What you do not have to do is go looking
for which one: three things report it, and each names the method.

* an operation the document gained is an abstract method nobody implements, so
  `Implementation()` raises `TypeError` naming it, at startup rather than on
  the first request that reaches it;
* an operation whose parameters or response changed is an incompatible
  override, so `mypy impl.py` prints both signatures side by side;
* an operation the document *dropped* leaves a method with nothing to override,
  which is what `@override` on each of them is for -- without it that method
  would sit here routing nowhere and type-checking fine.

Copy a new method from its abstract declaration in `bookstore_server/api/`,
which carries the signature and the documentation the document gave it.
"""

from __future__ import annotations

import datetime
from typing import Annotated, Literal, override

from pydantic import Field

from bookstore_server import Api, create_app
from bookstore_server.models import Book, Delivery, Publication, Review, ShelfSlot
from bookstore_server.runtime import Credential


class Implementation(Api):
    """Bookstore API."""

    @override
    async def post_books(
        self,
        *,
        body: Book,
        credential: Credential,
    ) -> Book:
        """Add a book"""
        raise NotImplementedError

    @override
    async def get_books(
        self,
        *,
        credential: Credential,
        offset: int = 0,
        limit: Annotated[int, Field(le=100)] = 20,
    ) -> list[Book]:
        """List Books"""
        raise NotImplementedError

    @override
    async def get_books_isbn(
        self,
        *,
        isbn: Annotated[str, Field(pattern=r'^\d{13}$')],
        credential: Credential | None,
    ) -> Book:
        """Retrieve a book"""
        raise NotImplementedError

    @override
    async def delete_books_isbn(
        self,
        *,
        isbn: Annotated[str, Field(pattern=r'^\d{13}$')],
        credential: Credential,
    ) -> None:
        """Delete a book"""
        raise NotImplementedError

    @override
    async def post_shelves(
        self,
        *,
        body: list[Book] | Review,
        credential: Credential,
    ) -> Annotated[list[ShelfSlot], Field(min_length=1)]:
        """Create a shelf"""
        raise NotImplementedError

    @override
    async def get_deliveries(
        self,
        *,
        credential: Credential,
        since: datetime.datetime,
        status: Literal['pending', 'sent', 'delivered'] | None = None,
        city: str | None = None,
        carrier: Literal['royal-mail-tracked-48-signed-for', 'dpd-next-day-before-noon', 'evri-standard'] | None = None,
    ) -> list[Delivery]:
        """List deliveries"""
        raise NotImplementedError

    @override
    async def get_publications(
        self,
        *,
        credential: Credential,
    ) -> list[Publication]:
        """List publications"""
        raise NotImplementedError

    @override
    async def get_search(
        self,
        *,
        credential: Credential,
        q: str | float,
        sort: Annotated[str, Field(pattern=r'^-?(title|isbn|price)$')] = 'title',
    ) -> list[Book]:
        """Search the catalogue"""
        raise NotImplementedError


app = create_app(Implementation())
