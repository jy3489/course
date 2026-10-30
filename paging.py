"""Bounded result pages and cursors bound to a particular authorized query."""

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class Page(Generic[T]):
    items: list[T]
    next_cursor: str | None = None


def page_size(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_PAGE_SIZE:
        raise ValueError("page size must be between 1 and 100")
    return value


def _scope(scope: object) -> str:
    return hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()


def encode_cursor(scope: object, after: str) -> str:
    return base64.urlsafe_b64encode(
        json.dumps([1, _scope(scope), after]).encode()
    ).decode()


def decode_cursor(scope: object, value: str | None) -> str | None:
    if value is None:
        return None
    try:
        if len(value) > 4096:
            raise ValueError()
        version, fingerprint, after = json.loads(
            base64.b64decode(value, altchars=b"-_", validate=True)
        )
        if (
            version != 1
            or fingerprint != _scope(scope)
            or not isinstance(after, str)
            or not after
            or "/" in after
        ):
            raise ValueError()
        return after
    except (ValueError, TypeError, UnicodeError):
        raise ValueError(
            "invalid cursor or cursor belongs to a different query"
        ) from None
