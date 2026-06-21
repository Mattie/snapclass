from __future__ import annotations

from typing import Optional

from .schemas import Missing

Trilean = Optional[bool]


class Dict(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        if name == "snapshot":
            object.__setattr__(self, name, value)
            return
        self[name] = value

    @classmethod
    def to_yaml(cls, representer, value):
        return representer.represent_dict(value)


class List(list):
    @classmethod
    def to_yaml(cls, representer, value):
        return representer.represent_list(value)


__all__ = ["Dict", "List", "Missing", "Trilean"]
