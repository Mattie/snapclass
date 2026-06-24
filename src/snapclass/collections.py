from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterator
from typing import Any

from . import sessions
from .stash import Stash

_missing_argument = object()


class CollectionDescriptor:
    def __get__(self, obj: object, cls: type) -> "Collection":
        return Collection(cls)


class Collection:
    def __init__(self, cls: type, stash: Stash | None = None) -> None:
        self.model = cls
        self._stash = stash

    def __call__(self, stash: Stash | str | os.PathLike[str]) -> "Collection":
        from .schemas import _coerce_stash

        return Collection(self.model, _coerce_stash(stash))

    def get(self, *args: Any, **kwargs: Any) -> Any:
        from .schemas import _attach_snapshot

        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        instance = self._empty_instance(*args, **kwargs)
        _attach_snapshot(instance, self.model.__snapclass_config__, self._stash)
        instance.snapshot.load(_initial=True)
        return instance

    def get_or_none(self, *args: Any, **kwargs: Any) -> Any | None:
        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        try:
            return self.get(*args, **kwargs)
        except FileNotFoundError:
            return None

    def get_or_create(self, *args: Any, **kwargs: Any) -> Any:
        from .schemas import _attach_snapshot, _mark_snapshot_ready, _write_lock_for

        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        instance = self._empty_instance(*args, **kwargs, include_defaults=True)
        _attach_snapshot(instance, self.model.__snapclass_config__, self._stash)
        initial_path = instance.snapshot._require_path()
        with _write_lock_for(initial_path):
            if instance.snapshot.exists:
                instance.snapshot.load(_initial=True)
                return instance
        _mark_snapshot_ready(instance)
        current_path = instance.snapshot._require_path()
        with _write_lock_for(current_path):
            if instance.snapshot.exists:
                instance.snapshot.load()
            else:
                instance.snapshot.save()
            return instance

    def all(self, *, _exclude: str = "") -> Iterator[Any]:
        from .schemas import (
            _PatternMatcher,
            _attach_snapshot,
            _has_path_value,
        )

        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        if not self.model.__snapclass_config__.pattern:
            raise RuntimeError("'pattern' must be set")
        try:
            matcher = _PatternMatcher(self.model, self._stash)
        except ValueError as exc:
            raise ValueError(
                f"Unable to scan {self.model.__name__}: {exc}"
            ) from exc
        for path in matcher.iter_candidates():
            if path.is_dir():
                continue
            values = matcher.values_from(path)
            if _exclude and values and str(values[0]).startswith(_exclude):
                continue
            if matcher.has_recursive_wildcard or _has_path_value(values):
                instance = self._empty_instance(*values)
                _attach_snapshot(instance, self.model.__snapclass_config__, self._stash)
                instance.snapshot.path = path
                instance.snapshot.load(_initial=True)
                yield instance
            else:
                yield self.get(*values)

    def filter(self, *, _exclude: str = "", **query: Any) -> Iterator[Any]:
        from .schemas import _lookup

        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        for item in self.all(_exclude=_exclude):
            if all(_lookup(item, key) == value for key, value in query.items()):
                yield item

    def _empty_instance(self, *args: Any, include_defaults: bool = False, **kwargs: Any) -> Any:
        from .schemas import (
            Missing,
            _coerce,
            _field_default,
            _is_missing,
            _placeholder_for,
        )

        instance = self.model.__new__(self.model)
        fields = [f for f in dataclasses.fields(self.model) if f.init]
        hints = self.model.__snapclass_config__.type_hints
        pattern = self.model.__snapclass_config__.pattern or ""
        arg_iter = iter(args)
        for field in fields:
            try:
                value = next(arg_iter)
            except StopIteration:
                value = kwargs.get(field.name, _missing_argument)
            if value is _missing_argument:
                default = _field_default(field)
                if include_defaults:
                    value = default
                elif not _is_missing(default):
                    value = default
                elif _placeholder_for(field.name) in pattern:
                    value = default
                    if _is_missing(value):
                        raise TypeError(
                            "Collection.get() missing required placeholder field "
                            f"argument: '{field.name}'"
                        )
                else:
                    value = Missing
            elif _placeholder_for(field.name) in pattern:
                value = _coerce(value, hints.get(field.name))
            object.__setattr__(instance, field.name, value)
        object.__setattr__(instance, "_snapclass_initializing", False)
        return instance


__all__ = ["Collection", "CollectionDescriptor"]
