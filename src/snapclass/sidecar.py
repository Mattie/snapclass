from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil


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


def markdown(
    pattern: str | None = None,
    *,
    field: str | None = None,
    default: str | None = None,
    encoding: str = "utf-8",
) -> "SidecarDescriptor":
    return SidecarDescriptor(pattern, field=field, default=default, encoding=encoding)


@dataclass(frozen=True)
class SidecarDescriptor:
    pattern: str | None = None
    field: str | None = None
    default: str | None = None
    encoding: str = "utf-8"

    def __get__(self, instance: object | None, owner: type | None = None):
        if instance is None:
            return self
        return SidecarFile(
            instance,
            self.pattern,
            field=self.field,
            default=self.default,
            encoding=self.encoding,
        )


def reconcile_before_save(instance: object, metadata_path: Path) -> None:
    snapshot = getattr(instance, "snapshot", None)
    previous_metadata_path = getattr(snapshot, "_loaded_path", None)
    if previous_metadata_path is None or previous_metadata_path == metadata_path:
        return

    for descriptor in _descriptors_for(type(instance)):
        sidecar_file = SidecarFile(
            instance,
            descriptor.pattern,
            field=descriptor.field,
            default=descriptor.default,
            encoding=descriptor.encoding,
        )
        try:
            relative = sidecar_file.relative_path
        except ValueError as exc:
            if "requires a pattern" in str(exc):
                continue
            raise

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


class SidecarFile:
    def __init__(
        self,
        instance: object,
        pattern: str | None = None,
        *,
        field: str | None = None,
        default: str | None = None,
        encoding: str = "utf-8",
    ) -> None:
        self._instance = instance
        self._pattern = pattern
        self._field = field
        self._default = default
        self._encoding = encoding

    @property
    def relative_path(self) -> Path:
        relative = Path(self._relative_pattern().format(self=self._instance))
        if relative.is_absolute():
            raise ValueError(f"Sidecar paths must be relative by default: {relative}")
        if ".." in relative.parts:
            raise ValueError(f"Sidecar paths cannot traverse above metadata: {relative}")
        return relative

    @property
    def path(self) -> Path:
        snapshot = getattr(self._instance, "snapshot", None)
        if snapshot is None:
            raise RuntimeError("Sidecars require a stashed instance")
        return snapshot.path.parent / self.relative_path

    @property
    def relpath(self) -> Path:
        snapshot = getattr(self._instance, "snapshot", None)
        if snapshot is None:
            raise RuntimeError("Sidecars require a stashed instance")
        base = snapshot.path.parent
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

    def read(self, default: str | None = None) -> str:
        if not self.path.exists():
            if default is not None:
                return default
            snapshot = getattr(self._instance, "snapshot", None)
            metadata_path = snapshot.path if snapshot is not None else Path()
            raise SidecarMissingError(
                self.path,
                metadata_path=metadata_path,
                relative_path=self.relpath,
                field=self._field,
            )
        return self.path.read_text(encoding=self._encoding)

    def write(self, text: str, *, save_metadata: bool = True) -> None:
        snapshot = getattr(self._instance, "snapshot", None)
        if save_metadata and self._field and snapshot is not None:
            snapshot._check_write_conflict(snapshot._require_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding=self._encoding)
        if self._field:
            object.__setattr__(self._instance, self._field, self.relpath.as_posix())
            if save_metadata and snapshot is not None:
                snapshot.save()

    def __fspath__(self) -> str:
        return str(self.path)

    def _relative_pattern(self) -> str:
        if self._field:
            value = getattr(self._instance, self._field, None)
            if value:
                return str(value)
        if self._pattern:
            return self._pattern
        if self._default:
            return self._default
        raise ValueError("Sidecar requires a pattern, default, or populated pointer field")

    def _has_pointer_value(self) -> bool:
        if not self._field:
            return False
        return bool(getattr(self._instance, self._field, None))


def _descriptors_for(cls: type) -> list[SidecarDescriptor]:
    descriptors: list[SidecarDescriptor] = []
    for base in reversed(cls.__mro__):
        for value in vars(base).values():
            if isinstance(value, SidecarDescriptor):
                descriptors.append(value)
    return descriptors
