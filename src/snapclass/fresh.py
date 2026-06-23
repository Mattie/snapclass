from __future__ import annotations

import copy as _copy
from collections import Counter, defaultdict, deque
from collections.abc import Callable
from dataclasses import field
from typing import Any

_MISSING = object()


class _Fresh:
    def __call__(self, factory: Callable[[], Any]):
        return field(default_factory=factory)

    @property
    def List(self):
        return field(default_factory=list)

    @property
    def Dict(self):
        return field(default_factory=dict)

    @property
    def Set(self):
        return field(default_factory=set)

    @property
    def Deque(self):
        return field(default_factory=deque)

    @property
    def Counter(self):
        return field(default_factory=Counter)

    def DefaultDict(
        self,
        factory: Callable[[], Any] | object = _MISSING,
        *,
        value: Callable[[], Any] | object = _MISSING,
    ):
        if factory is not _MISSING and value is not _MISSING:
            raise TypeError(
                "Fresh.DefaultDict accepts either a positional factory or value=, "
                "not both"
            )
        default_factory = value if value is not _MISSING else factory
        if default_factory is _MISSING:
            raise TypeError("Fresh.DefaultDict requires a missing-value factory")
        return field(default_factory=lambda: defaultdict(default_factory))

    def copy(self, template: Any):
        return field(default_factory=lambda: _copy.deepcopy(template))


Fresh = _Fresh()

__all__ = ["Fresh"]
