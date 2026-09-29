"""What a renderer returns: the document it built, and everything it had to leave out."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from raml_document.model import Document

__all__ = ['Report']


@dataclass(slots=True)
class Report:
    """A rendered document, and everything the renderer could not express."""

    document: Document
    #: One `at: what` entry per thing left out, in the order met.
    dropped: list[str] = field(default_factory=list)

    def to_raml(self) -> str:
        return self.document.to_raml()
