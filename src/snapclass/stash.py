from __future__ import annotations

import os
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import safe_path_placeholder


@dataclass(frozen=True)
class _Resolved:
    path: Path
    source: str
    is_external: bool


@dataclass(frozen=True)
class Stash:
    value: str | os.PathLike[str]
    env: str | None = None
    _parent: "Stash | None" = field(default=None, repr=False, compare=False)
    _bindings: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    _resolved: _Resolved | None = field(default=None, init=False, repr=False, compare=False)

    def __truediv__(self, child: str | os.PathLike[str] | "Stash") -> "Stash":
        if isinstance(child, Stash):
            return child._reparent(self)
        return Stash(child, _parent=self)

    def bind(self, **values: Any) -> "Stash":
        bindings = {**self._bindings, **values}
        parent = self._parent.bind(**values) if self._parent else None
        return Stash(self.value, env=self.env, _parent=parent, _bindings=bindings)

    def refresh(self) -> "Stash":
        parent = self._parent.refresh() if self._parent else None
        return Stash(self.value, env=self.env, _parent=parent, _bindings=dict(self._bindings))

    def _reparent(self, parent: "Stash") -> "Stash":
        if self._parent is None:
            return Stash(self.value, env=self.env, _parent=parent, _bindings=dict(self._bindings))
        return Stash(
            self.value,
            env=self.env,
            _parent=self._parent._reparent(parent),
            _bindings=dict(self._bindings),
        )

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
