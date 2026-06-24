from __future__ import annotations

import builtins
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from typing import Any, Literal, cast

from .stash import Stash

_Kind = Literal["text", "bytes"]
_StashLike = Stash | str | os.PathLike[str]
_MISSING = object()
_OVERRIDES_ATTR = "__snapclass_sidecar_overrides__"


class SidecarMissingError(FileNotFoundError):
    def __init__(
        self,
        path: Path,
        *,
        metadata_path: Path,
        relative_path: Path,
        field: str | None = None,
    ) -> None:
        self.path = path
        self.metadata_path = metadata_path
        self.relative_path = relative_path
        self.field = field
        field_text = f" from pointer field {field!r}" if field else ""
        super().__init__(
            f"Missing sidecar {path} referenced{field_text} by metadata "
            f"{metadata_path}; expected relative path {relative_path.as_posix()!r}"
        )


def text(
    pattern: str | None = None,
    *,
    field: str | None = None,
    default: str | None = None,
    encoding: str = "utf-8",
    stash: _StashLike | None = None,
) -> str:
    return cast(
        str,
        SidecarDescriptor(
            "text",
            pattern,
            field=field,
            default=default,
            encoding=encoding,
            stash=_coerce_stash(stash),
        ),
    )


def bytes(
    pattern: str | None = None,
    *,
    field: str | None = None,
    default: str | None = None,
    stash: _StashLike | None = None,
) -> builtins.bytes:
    return cast(
        builtins.bytes,
        SidecarDescriptor(
            "bytes",
            pattern,
            field=field,
            default=default,
            stash=_coerce_stash(stash),
        ),
    )


@dataclass(frozen=True)
class SidecarDescriptor:
    kind: _Kind
    pattern: str | None = None
    field: str | None = None
    default: str | None = None
    encoding: str = "utf-8"
    stash: Stash | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        """Remember the model attribute name for suppressed in-memory values."""
        object.__setattr__(self, "_name", name)

    def __get__(self, instance: object | None, owner: type | None = None):
        if instance is None:
            return self
        return self.value(instance)

    def __set__(self, instance: object, value: str | builtins.bytes) -> None:
        self.snapshot(instance).write(value)

    def snapshot(self, instance: object) -> "SidecarSnapshot":
        return SidecarSnapshot(instance, self)

    def value(self, instance: object) -> "SidecarText | SidecarBytes":
        snapshot = self.snapshot(instance)
        override = self._override_value(instance)
        if override is not _MISSING:
            if self.kind == "text":
                return SidecarText(cast(str, override), snapshot)
            return SidecarBytes(cast(builtins.bytes, override), snapshot)
        if self.kind == "text":
            return SidecarText(snapshot.read(default=""), snapshot)
        return SidecarBytes(snapshot.read(default=b""), snapshot)

    def _set_override(self, instance: object, value: str | builtins.bytes) -> None:
        """Store a sidecar assignment in memory without writing sidecar files."""
        if self.kind == "text" and not isinstance(value, str):
            raise TypeError("Text sidecars require str values")
        if self.kind == "bytes" and not isinstance(
            value,
            (builtins.bytes, bytearray, memoryview),
        ):
            raise TypeError("Bytes sidecars require bytes-like values")
        name = getattr(self, "_name", None)
        if name is None:
            return
        overrides = dict(getattr(instance, _OVERRIDES_ATTR, {}))
        overrides[name] = (
            builtins.bytes(value) if self.kind == "bytes" else value
        )
        object.__setattr__(instance, _OVERRIDES_ATTR, overrides)

    def _clear_override(self, instance: object) -> None:
        """Remove an in-memory sidecar assignment for this descriptor."""
        name = getattr(self, "_name", None)
        if name is None:
            return
        overrides = dict(getattr(instance, _OVERRIDES_ATTR, {}))
        if name not in overrides:
            return
        del overrides[name]
        object.__setattr__(instance, _OVERRIDES_ATTR, overrides)

    def _override_value(self, instance: object) -> object:
        """Return a suppressed in-memory value or the missing sentinel."""
        name = getattr(self, "_name", None)
        if name is None:
            return _MISSING
        return getattr(instance, _OVERRIDES_ATTR, {}).get(name, _MISSING)


def clear_overrides(instance: object) -> None:
    """Clear all suppressed sidecar assignments for an instance."""
    object.__setattr__(instance, _OVERRIDES_ATTR, {})


def flush_overrides(instance: object) -> None:
    """Write suppressed sidecar assignments as part of an enclosing save."""
    overrides = dict(getattr(instance, _OVERRIDES_ATTR, {}))
    if not overrides:
        return
    descriptors = {
        getattr(descriptor, "_name", None): descriptor
        for descriptor in _descriptors_for(type(instance))
    }
    for name, value in overrides.items():
        descriptor = descriptors.get(name)
        if descriptor is not None:
            descriptor.snapshot(instance).write(value, save_metadata=False)
            descriptor._clear_override(instance)


class SidecarText(str):
    snapshot: "SidecarSnapshot"

    def __new__(cls, value: str, snapshot: "SidecarSnapshot") -> "SidecarText":
        instance = str.__new__(cls, value)
        instance.snapshot = snapshot
        return instance


class SidecarBytes(builtins.bytes):
    snapshot: "SidecarSnapshot"

    def __new__(
        cls,
        value: builtins.bytes,
        snapshot: "SidecarSnapshot",
    ) -> "SidecarBytes":
        instance = builtins.bytes.__new__(cls, value)
        instance.snapshot = snapshot
        return instance


def _coerce_stash(value: _StashLike | None) -> Stash | None:
    if value is None or isinstance(value, Stash):
        return value
    return Stash(value)


def reconcile_before_save(instance: object, metadata_path: Path) -> None:
    snapshot = getattr(instance, "snapshot", None)
    previous_metadata_path = getattr(snapshot, "_loaded_path", None)
    if previous_metadata_path is None or previous_metadata_path == metadata_path:
        return

    for descriptor in _descriptors_for(type(instance)):
        sidecar_file = descriptor.snapshot(instance)
        try:
            relative = sidecar_file.relative_path
        except ValueError as exc:
            if "requires a pattern" in str(exc):
                continue
            raise

        if descriptor.stash is not None:
            base_path = sidecar_file._base_path()
            previous_path = base_path / relative
            current_path = base_path / relative
        else:
            previous_path = previous_metadata_path.parent / relative
            current_path = metadata_path.parent / relative
        if previous_path == current_path or not previous_path.exists():
            continue
        if current_path.exists():
            raise FileExistsError(
                f"Cannot move sidecar {previous_path} to {current_path}: "
                "destination already exists"
            )
        current_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(os.fspath(previous_path), os.fspath(current_path))


class SidecarSnapshot:
    def __init__(self, instance: object, descriptor: SidecarDescriptor) -> None:
        self._instance = instance
        self._descriptor = descriptor

    @property
    def stash(self) -> Stash | None:
        explicit_stash = self._explicit_stash()
        if explicit_stash is not None:
            return explicit_stash
        parent = self._parent_snapshot()
        return parent.stash if parent is not None else None

    @property
    def relative_path(self) -> Path:
        relative = Path(self._relative_pattern().format(self=self._instance))
        if relative.is_absolute():
            raise ValueError(f"Sidecar paths must be relative by default: {relative}")
        if ".." in relative.parts:
            raise ValueError(f"Sidecar paths cannot traverse above their root: {relative}")
        return relative

    @property
    def path(self) -> Path:
        return self._base_path() / self.relative_path

    @property
    def relpath(self) -> Path:
        base = self._base_path()
        try:
            return Path(os.path.relpath(self.path, base))
        except ValueError:
            try:
                return self.path.relative_to(base)
            except ValueError:
                return self.path

    def exists(self) -> bool:
        return self.path.exists()

    @property
    def stale(self) -> bool:
        return self._has_pointer_value() and not self.path.exists()

    def read(self, default: Any = _MISSING) -> str | builtins.bytes:
        if not self.path.exists():
            if default is not _MISSING:
                return default
            snapshot = getattr(self._instance, "snapshot", None)
            metadata_path = snapshot._require_path() if snapshot is not None else Path()
            raise SidecarMissingError(
                self.path,
                metadata_path=metadata_path,
                relative_path=self.relpath,
                field=self._field,
            )
        if self._descriptor.kind == "text":
            return self.path.read_text(encoding=self._descriptor.encoding)
        return self.path.read_bytes()

    def write(
        self,
        value: str | builtins.bytes,
        *,
        save_metadata: bool = True,
    ) -> None:
        snapshot = getattr(self._instance, "snapshot", None)
        if save_metadata and self._field and snapshot is not None:
            snapshot._check_write_conflict(snapshot._require_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._descriptor.kind == "text":
            if not isinstance(value, str):
                raise TypeError("Text sidecars require str values")
            self.path.write_text(value, encoding=self._descriptor.encoding)
        else:
            if not isinstance(value, (builtins.bytes, bytearray, memoryview)):
                raise TypeError("Bytes sidecars require bytes-like values")
            self.path.write_bytes(builtins.bytes(value))
        if self._field:
            object.__setattr__(self._instance, self._field, self.relpath.as_posix())
            if save_metadata and snapshot is not None:
                snapshot.save()

    def __fspath__(self) -> str:
        return str(self.path)

    @property
    def _field(self) -> str | None:
        return self._descriptor.field

    def _relative_pattern(self) -> str:
        if self._descriptor.field:
            value = getattr(self._instance, self._descriptor.field, None)
            if value:
                return str(value)
        if self._descriptor.pattern:
            return self._descriptor.pattern
        if self._descriptor.default:
            return self._descriptor.default
        raise ValueError("Sidecar requires a pattern, default, or populated pointer field")

    def _has_pointer_value(self) -> bool:
        if not self._field:
            return False
        return bool(getattr(self._instance, self._field, None))

    def _base_path(self) -> Path:
        explicit_stash = self._explicit_stash()
        if explicit_stash is not None:
            return explicit_stash.path
        parent = self._parent_snapshot()
        if parent is None:
            raise RuntimeError("Sidecars require a stashed instance")
        return parent._require_path().parent

    def _explicit_stash(self) -> Stash | None:
        stash = self._descriptor.stash
        if stash is None:
            return None
        parent = self._parent_snapshot()
        parent_stash = parent.stash if parent is not None else None
        if parent_stash is None:
            return stash
        return stash._with_parent_context(parent_stash)

    def _parent_snapshot(self):
        return getattr(self._instance, "snapshot", None)


def _descriptors_for(cls: type) -> list[SidecarDescriptor]:
    descriptors: list[SidecarDescriptor] = []
    for base in reversed(cls.__mro__):
        for value in vars(base).values():
            if isinstance(value, SidecarDescriptor):
                descriptors.append(value)
    return descriptors
