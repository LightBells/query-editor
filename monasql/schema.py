"""
monasql.schema - Table, TVF, and runtime TableRef definitions.

Tables and TVFs describe the schema; TableRef is the *live reference*
returned by ``from_()`` / ``apply_()`` that gives you column access
via ``ref.column_name``.
"""
from __future__ import annotations

from typing import Optional

from .expr import Col, Star


class TableRef:
    """Runtime handle to a table/TVF alias inside a query.

    ``ref.name`` yields ``Col('name', alias)`` so you can write
    ``u.name``, ``s.total_purchases`` etc.
    """

    def __init__(
        self,
        source_name: str,
        alias: str,
        columns: list[str] | None = None,
    ):
        object.__setattr__(self, "_source_name", source_name)
        object.__setattr__(self, "_alias", alias)
        object.__setattr__(self, "_columns", columns or [])

    # attribute access → Col
    def __getattr__(self, name: str) -> Col:
        if name.startswith("_"):
            raise AttributeError(name)
        return Col(name, self._alias)

    @property
    def star(self) -> Star:
        return Star(self._alias)

    def __repr__(self) -> str:
        return f"TableRef({self._source_name} AS {self._alias})"


class Table:
    """Static table definition — name and optional column list."""

    def __init__(
        self,
        name: str,
        columns: list[str] | None = None,
        schema: str | None = None,
    ):
        self.name = name
        self.columns = columns or []
        self.schema = schema

    @property
    def full_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name

    def __repr__(self) -> str:
        return f"Table({self.full_name})"


class TVF:
    """Table-Valued Function definition."""

    def __init__(
        self,
        name: str,
        params: list[str] | None = None,
        columns: list[str] | None = None,
        schema: str | None = None,
    ):
        self.name = name
        self.params = params or []
        self.columns = columns or []
        self.schema = schema

    @property
    def full_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name

    def __repr__(self) -> str:
        return f"TVF({self.full_name})"
