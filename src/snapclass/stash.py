from __future__ import annotations

import os
import string
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from . import formatters as _formatters
from . import serializers as _serializers
from .paths import safe_path_placeholder

_FormatterClass = type[_formatters.FileFormatter] | type[_formatters.Formatter]
_FormatterPolicy = Mapping[str, _FormatterClass]
_SerializerPolicy = Mapping[type | str, type[_serializers.Serializer]]


@dataclass(frozen=True)
class _Resolved:
    path: Path
    source: str
    is_external: bool


@dataclass(frozen=True)
class Stash:
    value: str | os.PathLike[str]
    env: str | None = None
    formatters: _FormatterPolicy | None = field(default=None, compare=False)
    serializers: _SerializerPolicy | None = field(default=None, compare=False)
    minimal_diffs: bool | None = field(default=None, compare=False)
    write_delay: float | None = field(default=None, compare=False)
    _parent: "Stash | None" = field(default=None, repr=False, compare=False)
    _bindings: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    _resolved: _Resolved | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "formatters",
            MappingProxyType(_formatters.normalize_formatters(self.formatters)),
        )
        object.__setattr__(
            self,
            "serializers",
            MappingProxyType(_serializers.normalize_serializers(self.serializers)),
        )

    def __truediv__(self, child: str | os.PathLike[str] | "Stash") -> "Stash":
        if isinstance(child, Stash):
            return child._reparent(self)
        return Stash(child, _parent=self)

    def bind(self, **values: Any) -> "Stash":
        bindings = {**self._bindings, **values}
        parent = self._parent.bind(**values) if self._parent else None
        return self._copy(_parent=parent, _bindings=bindings)

    def refresh(self) -> "Stash":
        parent = self._parent.refresh() if self._parent else None
        return self._copy(_parent=parent, _bindings=dict(self._bindings))

    def with_formatter(
        self,
        extension: str,
        formatter: _FormatterClass,
    ) -> "Stash":
        return self.with_formatters({extension: formatter})

    def with_formatters(
        self,
        formatters: _FormatterPolicy,
    ) -> "Stash":
        merged = {**self.formatters, **_formatters.normalize_formatters(formatters)}
        return self._copy(formatters=merged)

    def with_serializer(
        self,
        type_: type | str,
        serializer: type[_serializers.Serializer],
    ) -> "Stash":
        return self.with_serializers({type_: serializer})

    def with_serializers(
        self,
        serializers: _SerializerPolicy,
    ) -> "Stash":
        merged = {**self.serializers, **_serializers.normalize_serializers(serializers)}
        return self._copy(serializers=merged)

    def with_options(
        self,
        *,
        minimal_diffs: bool | None = None,
        write_delay: float | None = None,
    ) -> "Stash":
        return self._copy(
            minimal_diffs=self.minimal_diffs if minimal_diffs is None else minimal_diffs,
            write_delay=self.write_delay if write_delay is None else write_delay,
        )

    def effective_formatters(self) -> dict[str, type[_formatters.FileFormatter]]:
        merged = self._parent.effective_formatters() if self._parent else {}
        merged.update(self.formatters)
        return merged

    def effective_serializers(self) -> dict[type | str, type[_serializers.Serializer]]:
        merged = self._parent.effective_serializers() if self._parent else {}
        merged.update(self.serializers)
        return merged

    def effective_minimal_diffs(self) -> bool | None:
        if self.minimal_diffs is not None:
            return self.minimal_diffs
        return self._parent.effective_minimal_diffs() if self._parent else None

    def effective_write_delay(self) -> float | None:
        if self.write_delay is not None:
            return self.write_delay
        return self._parent.effective_write_delay() if self._parent else None

    def _reparent(self, parent: "Stash") -> "Stash":
        if self._parent is None:
            return self._copy(_parent=parent, _bindings=dict(self._bindings))
        return self._copy(
            _parent=self._parent._reparent(parent),
            _bindings=dict(self._bindings),
        )

    def _copy(self, **overrides: Any) -> "Stash":
        values = {
            "value": self.value,
            "env": self.env,
            "formatters": self.formatters,
            "serializers": self.serializers,
            "minimal_diffs": self.minimal_diffs,
            "write_delay": self.write_delay,
            "_parent": self._parent,
            "_bindings": dict(self._bindings),
        }
        values.update(overrides)
        return Stash(**values)

    @property
    def path(self) -> Path:
        return self.resolve()

    @property
    def source(self) -> str:
        if self._resolved is not None:
            return self._resolved.source
        return self._current_source()

    @property
    def is_external(self) -> bool:
        if self._resolved is not None:
            return self._resolved.is_external
        own = self._own_path()
        return self._parent is not None and (own.is_absolute() or _is_home_relative(own))

    def resolve(self) -> Path:
        if self._resolved is not None:
            return self._resolved.path
        own = self._own_path()
        is_external = self._parent is not None and (own.is_absolute() or _is_home_relative(own))
        if self._parent is None or own.is_absolute() or _is_home_relative(own):
            path = own.expanduser().resolve()
        else:
            self._reject_traversal(own)
            path = (self._parent.resolve() / own).resolve()
        object.__setattr__(
            self,
            "_resolved",
            _Resolved(path=path, source=self._current_source(), is_external=is_external),
        )
        return path

    def describe(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "source": self.source,
            "env": self.env,
            "is_external": self.is_external,
            "parent": self._parent.describe() if self._parent else None,
        }

    def _own_path(self) -> Path:
        raw_text = self._raw_text()
        missing = self._missing_placeholder_names()
        if missing:
            names = ", ".join(missing)
            raise ValueError(f"Unbound stash placeholder(s): {names}")
        try:
            bindings = {
                name: safe_path_placeholder(name, value)
                for name, value in self._bindings.items()
            }
            text = raw_text.format(**bindings)
        except KeyError as exc:
            raise ValueError(f"Unbound stash placeholder: {exc.args[0]}") from exc
        path = Path(text)
        if not path.is_absolute() and not _is_home_relative(path):
            self._reject_traversal(path)
        return path

    def _raw_text(self) -> str:
        raw = os.getenv(self.env) if self.env and os.getenv(self.env) else self.value
        return os.fspath(raw)

    def _current_source(self) -> str:
        if self.env and os.getenv(self.env):
            return f"env:{self.env}"
        return "constructor"

    def _missing_placeholder_names(self) -> list[str]:
        names: list[str] = []
        if self._parent is not None:
            names.extend(self._parent._missing_placeholder_names())
        for name in _missing_placeholders(self._raw_text(), self._bindings):
            if name not in names:
                names.append(name)
        return names

    @staticmethod
    def _reject_traversal(path: Path) -> None:
        if ".." in path.parts:
            raise ValueError(f"Relative stash paths cannot traverse above their root: {path}")

    def __fspath__(self) -> str:
        return os.fspath(self.path)


def _is_home_relative(path: Path) -> bool:
    return bool(path.parts) and path.parts[0] == "~"


def _missing_placeholders(pattern: str, bindings: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for _, field_name, _, _ in string.Formatter().parse(pattern):
        if field_name is None:
            continue
        root_name = field_name.split(".", 1)[0].split("[", 1)[0]
        if root_name and root_name not in bindings and root_name not in names:
            names.append(root_name)
    return names
