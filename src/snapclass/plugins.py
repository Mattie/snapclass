from __future__ import annotations

from typing import Any

DECORATOR_SUFFIXES = (".snapclass",)
DECORATOR_FULLNAMES = {
    "snapclass.snapclass",
    "snapclass.decorators.snapclass",
    "snapclass.schemas.snapclass",
}


def mypy(version: str):
    try:
        from mypy.nodes import MDEF, SymbolTableNode, Var
        from mypy.plugin import ClassDefContext, Plugin
        from mypy.plugins.dataclasses import DataclassTransformer
        from mypy.types import AnyType, TypeOfAny
    except Exception:
        return MypyUnavailablePlugin

    try:
        from mypy.nodes import DataclassTransformSpec
    except Exception:
        DataclassTransformSpec = None  # type: ignore[assignment,misc]

    def make_any_member(ctx: ClassDefContext, name: str) -> None:
        member = Var(name, AnyType(TypeOfAny.unannotated))
        member.info = ctx.cls.info
        member.is_property = True
        ctx.cls.info.names[name] = SymbolTableNode(MDEF, member)

    def transform_dataclass(ctx: ClassDefContext) -> None:
        if DataclassTransformSpec is None:
            DataclassTransformer(ctx).transform()  # type: ignore[call-arg]
            return
        try:
            spec = DataclassTransformSpec()
            DataclassTransformer(ctx.cls, ctx.reason, spec, ctx.api).transform()
        except TypeError:
            DataclassTransformer(ctx).transform()  # type: ignore[call-arg]

    class SnapclassPlugin(Plugin):
        def get_class_decorator_hook(self, fullname: str):
            if fullname in DECORATOR_FULLNAMES:
                return self._process_snapclass_class
            return None

        def _process_snapclass_class(self, ctx: ClassDefContext) -> None:
            transform_dataclass(ctx)
            for name in ("snapshots", "snapshot"):
                make_any_member(ctx, name)

    return SnapclassPlugin


class MypyUnavailablePlugin:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(
            "Install mypy in the type-checking environment to use "
            "snapclass.plugins:mypy"
        )


__all__ = ["DECORATOR_SUFFIXES", "MypyUnavailablePlugin", "mypy"]
