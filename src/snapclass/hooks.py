from __future__ import annotations

import inspect
from collections.abc import Iterable
from functools import wraps
from typing import Any

from . import sessions
from .schemas import _wrap_mutables, frozen as disabled

LOAD_BEFORE_METHODS = ["__getattribute__", "__getitem__", "__iter__"]
SAVE_AFTER_METHODS = [
    "__setattr__",
    "__setitem__",
    "__delitem__",
    "append",
    "extend",
    "insert",
    "remove",
    "pop",
    "clear",
    "sort",
    "reverse",
    "popitem",
    "update",
]
FLAG = "_patched"


def get_snapshot(obj: Any) -> Any | None:
    missing = object()
    snapshot = getattr(obj, "snapshot", missing)
    return None if snapshot is missing else snapshot


def enabled(snapshot: Any, args: Iterable[Any]) -> bool:
    if not sessions.HOOKS_ENABLED or snapshot is None:
        return False
    if getattr(snapshot, "manual", False):
        return False
    name = _first_string_arg(args)
    if name is None:
        return True
    return name not in {"Meta", "snapshot"} and not name.startswith("_")


def load_before(cls: type, method: Any) -> Any:
    if getattr(method, FLAG, False):
        return method

    @wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        snapshot = get_snapshot(self)
        if _needs_reload(snapshot, args):
            _invoke(snapshot.load)
            _invoke(snapshot.save, _log=False)
        return method(self, *args, **kwargs)

    setattr(wrapper, FLAG, True)
    return wrapper


def save_after(cls: type, method: Any) -> Any:
    if getattr(method, FLAG, False):
        return method

    @wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        snapshot = get_snapshot(self)
        if _needs_reload(snapshot, args):
            _invoke(snapshot.load)
        result = method(self, *args, **kwargs)
        if enabled(snapshot, args):
            _invoke(snapshot.save)
            _invoke(snapshot.load, _log=False)
        return result

    setattr(wrapper, FLAG, True)
    return wrapper


def apply(instance: Any, snapshot: Any) -> None:
    if snapshot is not None and get_snapshot(instance) is None:
        object.__setattr__(instance, "snapshot", snapshot)
    _wrap_mutables(instance)


def _first_string_arg(args: Iterable[Any]) -> str | None:
    for value in args:
        return value if isinstance(value, str) else None
    return None


def _needs_reload(snapshot: Any, args: Iterable[Any]) -> bool:
    if not enabled(snapshot, args):
        return False
    return bool(getattr(snapshot, "exists", False) and getattr(snapshot, "modified", False))


def _invoke(method: Any, **optional_kwargs: Any) -> Any:
    signature = inspect.signature(method)
    return method(
        **{
            name: value
            for name, value in optional_kwargs.items()
            if name in signature.parameters
        }
    )


__all__ = [
    "FLAG",
    "LOAD_BEFORE_METHODS",
    "SAVE_AFTER_METHODS",
    "apply",
    "disabled",
    "enabled",
    "get_snapshot",
    "load_before",
    "save_after",
]
