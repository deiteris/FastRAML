"""A starting point for implementing Bookstore API.

**This file is yours.** It has no generated header because nothing regenerates
it: `raml-codegen` rewrites `bookstore_server/` and leaves this alone. Copy
it into your own project, fill in the methods, and delete this paragraph.

    uvicorn impl:app --reload

Every method raises `NotImplementedError` until you replace it. `abc` will not
let `Implementation()` be constructed while one is missing, so the document and
the implementation cannot drift apart without the server refusing to start.
"""

from __future__ import annotations

import datetime
from typing import Annotated, Literal

from pydantic import Field

from bookstore_server import Api, create_app
from bookstore_server.models import Book, Delivery, Publication, Review, ShelfSlot
from bookstore_server.runtime import Credential


class Implementation(Api):
    """Bookstore API."""

    async def post_books(
        self,
        *,
        body: Book,
        credential: Credential,
    ) -> Book:
        """Add a book"""
        raise NotImplementedError

    async def get_books(
        self,
        *,
        credential: Credential,
        offset: int = 0,
        limit: Annotated[int, Field(le=100)] = 20,
    ) -> list[Book]:
        """List Books"""
        raise NotImplementedError

    async def get_books_isbn(
        self,
        *,
        isbn: Annotated[str, Field(pattern=r'^\d{13}$')],
        credential: Credential | None,
    ) -> Book:
        """Retrieve a book"""
        raise NotImplementedError

    async def delete_books_isbn(
        self,
        *,
        isbn: Annotated[str, Field(pattern=r'^\d{13}$')],
        credential: Credential,
    ) -> None:
        """Delete a book"""
        raise NotImplementedError

    async def post_shelves(
        self,
        *,
        body: list[Book] | Review,
        credential: Credential,
    ) -> Annotated[list[ShelfSlot], Field(min_length=1)]:
        """Create a shelf"""
        raise NotImplementedError

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

    async def get_publications(
        self,
        *,
        credential: Credential,
    ) -> list[Publication]:
        """List publications"""
        raise NotImplementedError

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
