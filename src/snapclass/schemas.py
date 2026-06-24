from __future__ import annotations

import copy
import dataclasses
import datetime as _datetime
import enum
import inspect
import os
import re
import sys
import tempfile
import threading
import time
import types
import warnings
from collections import Counter, defaultdict, deque
from collections.abc import Iterator, Mapping, MutableMapping, Set as AbstractSet
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Union, get_args, get_origin, get_type_hints, is_typeddict

from . import formatters, serializers, sessions, sidecar
from .collections import Collection, CollectionDescriptor
from .paths import safe_path_placeholder
from .formatters import FileFormatter
from .stash import Stash, _is_home_relative

Missing = dataclasses.MISSING
_MISSING_TYPE = type(dataclasses.MISSING)
_UNKNOWN_DATA_ATTR = "__snapclass_unknown_data__"
_INFERRED_FIELDS_ATTR = "__snapclass_inferred_fields__"
_INFERRED_HINTS_ATTR = "__snapclass_inferred_hints__"
_DEFAULT_CACHE_ATTR = "__snapclass_default_cache__"
_PENDING_SIDECARS_ATTR = "__snapclass_pending_sidecars__"
_WRITE_LOCKS: dict[Path, threading.RLock] = {}
_WRITE_LOCKS_GUARD = threading.Lock()


class SnapclassError(Exception):
    pass


def _is_missing(value: Any) -> bool:
    return value is Missing or value is _MISSING_TYPE


class _CoercionError(Exception):
    def __init__(self, field_path: str, hint: Any, value: Any, cause: Exception) -> None:
        self.field_path = field_path
        self.hint = hint
        self.value = value
        super().__init__(str(cause))


@dataclasses.dataclass
class Config:
    pattern: str | None
    stash: Stash | None = None
    module_dir: Path | None = None
    manual: bool = False
    defaults: bool = False
    infer: bool = False
    fields: dict[str, Any] | None = None
    formatter: type[FileFormatter] | None = None
    minimal_diffs: bool | None = None
    write_delay: float | None = None
    unknown: str = "ignore"
    extras_field: str | None = None
    migrate: Callable[..., Mapping[str, Any] | None] | None = None
    conflict: str = "overwrite"
    type_hints: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Meta:
    snapshot_fields: dict[str, Any] | None = None
    snapshot_pattern: str | None = None
    snapshot_manual: bool = False
    snapshot_defaults: bool = False
    snapshot_infer: bool = False
    snapshot_stash: Any = None
    snapshot_formatter: Any = None
    snapshot_minimal_diffs: bool | None = None
    snapshot_write_delay: float | None = None
    snapshot_unknown: str = "ignore"
    snapshot_extras_field: str | None = None
    snapshot_migrate: Callable[..., Mapping[str, Any] | None] | None = None
    snapshot_conflict: str = "overwrite"


def snapclass(
    pattern: str | Callable | None = None,
    *,
    stash: Stash | None = None,
    manual: bool = False,
    defaults: bool = False,
    include_defaults: bool | None = None,
    infer: bool = False,
    fields: dict[str, Any] | None = None,
    formatter: type[FileFormatter] | None = None,
    minimal_diffs: bool | None = None,
    write_delay: float | None = None,
    unknown: str = "ignore",
    extras_field: str | None = None,
    migrate: Callable[..., Mapping[str, Any] | None] | None = None,
    conflict: str = "overwrite",
    **dataclass_kwargs: Any,
):
    if pattern is None:
        return dataclasses.dataclass(**dataclass_kwargs)

    if callable(pattern):
        return dataclasses.dataclass(pattern)

    def decorate(cls: type):
        if not dataclasses.is_dataclass(cls):
            cls = _dataclass_with_sidecars(cls, **dataclass_kwargs)
        unknown_policy = _normalize_unknown_policy(unknown, extras_field)
        conflict_policy = _normalize_conflict_policy(conflict)
        _validate_extras_field(cls, unknown_policy, extras_field)
        config = Config(
            pattern=pattern,
            stash=stash,
            module_dir=_module_dir_for(cls),
            manual=manual,
            defaults=defaults if include_defaults is None else include_defaults,
            infer=infer,
            fields=fields,
            formatter=formatter,
            minimal_diffs=minimal_diffs,
            write_delay=write_delay,
            unknown=unknown_policy,
            extras_field=extras_field,
            migrate=migrate,
            conflict=conflict_policy,
        )
        config.type_hints = _resolve_type_hints(cls)
        _install(cls, config)
        return cls

    return decorate

def create_model(
    cls: type,
    *,
    fields: dict[str, Any] | None = None,
    manual: bool | None = None,
    pattern: str | None = None,
    stash: Stash | str | os.PathLike[str] | None = None,
    defaults: bool | None = None,
    infer: bool | None = None,
    minimal_diffs: bool | None = None,
    write_delay: float | None = None,
    migrate: Callable[..., Mapping[str, Any] | None] | None = None,
    conflict: str | None = None,
) -> type:
    if not dataclasses.is_dataclass(cls):
        raise ValueError(f"{cls} must be a dataclass")

    meta = getattr(cls, "Meta", None)
    meta_pattern = getattr(meta, "snapshot_pattern", None) if meta is not None else None
    resolved_pattern = pattern if pattern is not None else meta_pattern
    resolved_fields = fields if fields is not None else (
        getattr(meta, "snapshot_fields", None) if meta is not None else None
    )
    resolved_manual = manual if manual is not None else (
        getattr(meta, "snapshot_manual", False) if meta is not None else False
    )
    if resolved_pattern is None:
        resolved_manual = True
    resolved_defaults = defaults if defaults is not None else (
        getattr(meta, "snapshot_defaults", False) if meta is not None else False
    )
    resolved_infer = infer if infer is not None else (
        getattr(meta, "snapshot_infer", False) if meta is not None else False
    )
    meta_stash = getattr(meta, "snapshot_stash", None) if meta is not None else None
    resolved_stash = stash if stash is not None else meta_stash
    if resolved_stash is not None:
        resolved_stash = _coerce_stash(resolved_stash)
    formatter = getattr(meta, "snapshot_formatter", None) if meta is not None else None
    resolved_minimal_diffs = minimal_diffs if minimal_diffs is not None else (
        getattr(meta, "snapshot_minimal_diffs", None) if meta is not None else None
    )
    resolved_write_delay = write_delay if write_delay is not None else (
        getattr(meta, "snapshot_write_delay", None) if meta is not None else None
    )
    unknown = getattr(meta, "snapshot_unknown", "ignore") if meta is not None else "ignore"
    extras_field = getattr(meta, "snapshot_extras_field", None) if meta is not None else None
    resolved_migrate = migrate if migrate is not None else (
        getattr(meta, "snapshot_migrate", None) if meta is not None else None
    )
    resolved_conflict = conflict if conflict is not None else (
        getattr(meta, "snapshot_conflict", "overwrite") if meta is not None else "overwrite"
    )
    _install_model_config(
        cls,
        pattern=resolved_pattern,
        stash=resolved_stash,
        manual=resolved_manual,
        defaults=resolved_defaults,
        infer=resolved_infer,
        fields=resolved_fields,
        formatter=formatter,
        minimal_diffs=resolved_minimal_diffs,
        write_delay=resolved_write_delay,
        unknown=unknown,
        extras_field=extras_field,
        migrate=resolved_migrate,
        conflict=resolved_conflict,
    )
    cls.Meta = Meta(
        snapshot_fields=resolved_fields,
        snapshot_pattern=resolved_pattern,
        snapshot_manual=resolved_manual,
        snapshot_defaults=resolved_defaults,
        snapshot_infer=resolved_infer,
        snapshot_stash=resolved_stash,
        snapshot_formatter=formatter,
        snapshot_minimal_diffs=resolved_minimal_diffs,
        snapshot_write_delay=resolved_write_delay,
        snapshot_unknown=_normalize_unknown_policy(unknown, extras_field),
        snapshot_extras_field=extras_field,
        snapshot_migrate=resolved_migrate,
        snapshot_conflict=_normalize_conflict_policy(resolved_conflict),
    )
    return cls


def _install_model_config(
    cls: type,
    *,
    pattern: str | None,
    stash: Stash | None = None,
    manual: bool = False,
    defaults: bool = False,
    infer: bool = False,
    fields: dict[str, Any] | None = None,
    formatter: type[FileFormatter] | None = None,
    minimal_diffs: bool | None = None,
    write_delay: float | None = None,
    unknown: str = "ignore",
    extras_field: str | None = None,
    migrate: Callable[..., Mapping[str, Any] | None] | None = None,
    conflict: str = "overwrite",
) -> None:
    unknown_policy = _normalize_unknown_policy(unknown, extras_field)
    conflict_policy = _normalize_conflict_policy(conflict)
    _validate_extras_field(cls, unknown_policy, extras_field)
    config = Config(
        pattern=pattern,
        stash=stash,
        module_dir=_module_dir_for(cls),
        manual=True if pattern is None else manual,
        defaults=defaults,
        infer=infer,
        fields=fields,
        formatter=formatter,
        minimal_diffs=minimal_diffs,
        write_delay=write_delay,
        unknown=unknown_policy,
        extras_field=extras_field,
        migrate=migrate,
        conflict=conflict_policy,
    )
    config.type_hints = _resolve_type_hints(cls)
    _install(cls, config)


def auto(filename: str, **kwargs: Any) -> Any:
    """Map an arbitrary file to an inferred synchronized object."""
    kwargs["infer"] = True
    path = Path.cwd() / filename
    name = path.stem.strip(".").capitalize() or "Snapshot"

    def auto_repr(self: object) -> str:
        items = (
            f"{key}={value!r}"
            for key, value in self.__dict__.items()
            if key != "snapshot" and not key.startswith("_snapclass")
        )
        return f"{name}({', '.join(items)})"

    cls = type(name, (), {"__annotations__": {}, "__repr__": auto_repr})
    return snapclass(str(path), **kwargs)(cls)()


class Model:
    Meta: Meta = Meta()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "__snapclass_config__" in cls.__dict__:
            return
        meta = getattr(cls, "Meta", None)
        if not dataclasses.is_dataclass(cls):
            _dataclass_with_sidecars(cls)

        _install_model_config(
            cls,
            pattern=getattr(meta, "snapshot_pattern", None) if meta is not None else None,
            stash=getattr(meta, "snapshot_stash", None),
            manual=getattr(meta, "snapshot_manual", False),
            defaults=getattr(meta, "snapshot_defaults", False),
            infer=getattr(meta, "snapshot_infer", False),
            fields=getattr(meta, "snapshot_fields", None),
            formatter=getattr(meta, "snapshot_formatter", None),
            minimal_diffs=getattr(meta, "snapshot_minimal_diffs", None),
            write_delay=getattr(meta, "snapshot_write_delay", None),
            unknown=getattr(meta, "snapshot_unknown", "ignore"),
            extras_field=getattr(meta, "snapshot_extras_field", None),
            migrate=getattr(meta, "snapshot_migrate", None),
            conflict=getattr(meta, "snapshot_conflict", "overwrite"),
        )


def sync(
    instance: object,
    pattern: str,
    *,
    fields: dict[str, Any] | None = None,
    stash: Stash | None = None,
    manual: bool = False,
    defaults: bool = False,
    infer: bool = False,
    formatter: type[FileFormatter] | None = None,
    minimal_diffs: bool | None = None,
    write_delay: float | None = None,
    unknown: str = "ignore",
    extras_field: str | None = None,
    migrate: Callable[..., Mapping[str, Any] | None] | None = None,
    conflict: str = "overwrite",
) -> object:
    cls = instance.__class__
    unknown_policy = _normalize_unknown_policy(unknown, extras_field)
    conflict_policy = _normalize_conflict_policy(conflict)
    _validate_extras_field(cls, unknown_policy, extras_field)
    config = Config(
        pattern=pattern,
        stash=stash,
        module_dir=_module_dir_for(cls),
        manual=manual,
        defaults=defaults,
        infer=infer,
        fields=fields,
        formatter=formatter,
        minimal_diffs=minimal_diffs,
        write_delay=write_delay,
        unknown=unknown_policy,
        extras_field=extras_field,
        migrate=migrate,
        conflict=conflict_policy,
    )
    config.type_hints = _safe_type_hints(cls)
    if not hasattr(cls, "__snapclass_config__"):
        _install(cls, config)
    _attach_snapshot(instance, config)
    _mark_snapshot_ready(instance)
    if _auto_enabled(config, instance):
        instance.snapshot.save()
    return instance


def _dataclass_with_sidecars(cls: type, **dataclass_kwargs: Any) -> type:
    sidecars = _sidecar_descriptors_for(cls)
    original_annotations = getattr(cls, "__annotations__", None)
    if sidecars and original_annotations is not None:
        dataclass_annotations = dict(original_annotations)
        for name in sidecars:
            dataclass_annotations.pop(name, None)
        cls.__annotations__ = dataclass_annotations
    try:
        dataclass_cls = dataclasses.dataclass(cls, **dataclass_kwargs)
    finally:
        if original_annotations is not None:
            cls.__annotations__ = original_annotations
    if original_annotations is not None:
        dataclass_cls.__annotations__ = original_annotations
    return dataclass_cls


def _sidecar_descriptors_for(cls: type) -> dict[str, sidecar.SidecarDescriptor]:
    descriptors: dict[str, sidecar.SidecarDescriptor] = {}
    for base in reversed(cls.__mro__):
        for name, value in vars(base).items():
            if isinstance(value, sidecar.SidecarDescriptor):
                descriptors[name] = value
    return descriptors


def _sidecar_descriptor_for(
    cls: type,
    name: str,
) -> sidecar.SidecarDescriptor | None:
    return _sidecar_descriptors_for(cls).get(name)


def _pop_sidecar_values(cls: type, values: dict[str, Any]) -> dict[str, Any]:
    sidecar_values: dict[str, Any] = {}
    for name in _sidecar_descriptors_for(cls):
        if name in values:
            sidecar_values[name] = values.pop(name)
    return sidecar_values


def _apply_sidecar_values(
    instance: object,
    values: Mapping[str, Any],
    *,
    save_metadata: bool,
) -> None:
    descriptors = _sidecar_descriptors_for(instance.__class__)
    for name, value in values.items():
        descriptors[name].snapshot(instance).write(value, save_metadata=save_metadata)


@contextmanager
def frozen(*snapshots: object):
    previous = sessions.HOOKS_ENABLED
    sessions.HOOKS_ENABLED = False
    try:
        yield
    finally:
        sessions.HOOKS_ENABLED = previous
        if previous:
            for obj in snapshots:
                snapshot = getattr(obj, "snapshot", None)
                if snapshot is not None:
                    snapshot.save()


def _mark_snapshot_ready(instance: object) -> None:
    """Run ``__snapclass_ready__`` after snapshot attachment and setup settle."""
    snapshot = getattr(instance, "snapshot", None)
    if snapshot is None:
        return
    if getattr(snapshot, "_ready", False):
        return
    hook = getattr(instance, "__snapclass_ready__", None)
    expected_path = snapshot.path if hook is not None else None
    snapshot._ready = True
    try:
        _call_snapshot_lifecycle_hook(
            instance,
            "__snapclass_ready__",
            snapshot=snapshot,
        )
        _ensure_snapshot_path_unchanged(
            snapshot,
            expected_path,
            "__snapclass_ready__",
        )
    except Exception:
        snapshot._ready = False
        raise


def _mark_snapshot_loaded(instance: object, path: Path) -> None:
    """Run ``__snapclass_loaded__`` after file data is applied and tracked."""
    snapshot = getattr(instance, "snapshot", None)
    if snapshot is None:
        return
    hook = getattr(instance, "__snapclass_loaded__", None)
    expected_path = snapshot.path if hook is not None else None
    _call_snapshot_lifecycle_hook(
        instance,
        "__snapclass_loaded__",
        snapshot=snapshot,
        path=path,
    )
    _ensure_snapshot_path_unchanged(
        snapshot,
        expected_path,
        "__snapclass_loaded__",
    )


def _call_snapshot_lifecycle_hook(
    instance: object,
    hook_name: str,
    *,
    snapshot: "Snapshot",
    path: Path | None = None,
) -> None:
    """Invoke a snapclass lifecycle hook with suppressed autosave/reload hooks."""
    hook = getattr(instance, hook_name, None)
    if hook is None:
        return
    try:
        with _snapclass_hook_context(instance):
            if hook_name == "__snapclass_loaded__":
                hook(snapshot=snapshot, path=path)
            else:
                hook(snapshot=snapshot)
    except Exception as exc:
        location = f" at {path}" if path is not None else ""
        raise SnapclassError(
            f"Failed to run {hook_name} for {instance.__class__.__name__}"
            f"{location}: {exc}"
        ) from exc


@contextmanager
def _snapclass_hook_context(instance: object) -> Iterator[None]:
    """Suppress automatic saves and reloads for one instance during lifecycle hooks."""
    previous_loading = getattr(instance, "_snapclass_loading", False)
    previous_suppressed = getattr(instance, "_snapclass_hooks_suppressed", False)
    object.__setattr__(instance, "_snapclass_loading", True)
    object.__setattr__(instance, "_snapclass_hooks_suppressed", True)
    try:
        yield
    finally:
        object.__setattr__(instance, "_snapclass_hooks_suppressed", previous_suppressed)
        object.__setattr__(instance, "_snapclass_loading", previous_loading)


def _ensure_snapshot_path_unchanged(
    snapshot: "Snapshot",
    expected_path: Path | None,
    hook_name: str,
) -> None:
    """Raise when a lifecycle hook retargets the snapshot path."""
    if expected_path is None:
        return
    try:
        current_path = snapshot._require_path()
    except Exception as exc:
        raise SnapclassError(
            f"{hook_name} left snapshot path unresolved after lifecycle hook; "
            "normalize snapshot path fields before loading or creating "
            f"snapshots: {exc}"
        ) from exc
    if current_path == expected_path:
        return
    raise SnapclassError(
        f"{hook_name} changed snapshot path from {expected_path} to "
        f"{current_path}; normalize snapshot path fields before loading or "
        "creating snapshots"
    )


def _install(cls: type, config: Config) -> None:
    cls.__snapclass_config__ = config
    cls.snapshots = CollectionDescriptor()
    _install_object_file_methods(cls)

    original_init = cls.__init__

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        sidecar_values = _pop_sidecar_values(self.__class__, kwargs)
        object.__setattr__(self, "_snapclass_initializing", True)
        object.__setattr__(self, _PENDING_SIDECARS_ATTR, {})
        original_init(self, *args, **kwargs)
        _attach_snapshot(self, config)
        pending_sidecars = object.__getattribute__(self, "__dict__").pop(
            _PENDING_SIDECARS_ATTR,
            {},
        )
        _apply_sidecar_values(self, pending_sidecars, save_metadata=False)
        _apply_sidecar_values(self, sidecar_values, save_metadata=False)
        object.__setattr__(self, "_snapclass_initializing", False)
        automatic = _auto_enabled(config, self)
        if automatic and self.snapshot.exists:
            self.snapshot.load(_initial=True)
        else:
            _mark_snapshot_ready(self)
            if automatic:
                if self.snapshot.exists:
                    self.snapshot.load()
                else:
                    self.snapshot.save()

    cls.__init__ = __init__

    original_setattr = getattr(cls, "__setattr__", object.__setattr__)

    def __setattr__(self, name: str, value: Any) -> None:
        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        sidecar_descriptor = _sidecar_descriptor_for(self.__class__, name)
        if sidecar_descriptor is not None:
            snapshot = getattr(self, "snapshot", None)
            if snapshot is None or getattr(self, "_snapclass_initializing", False):
                pending = dict(getattr(self, _PENDING_SIDECARS_ATTR, {}))
                pending[name] = value
                object.__setattr__(self, _PENDING_SIDECARS_ATTR, pending)
                return
            if getattr(self, "_snapclass_hooks_suppressed", False):
                raise SnapclassError(
                    f"Cannot assign sidecar {name!r} during snapclass lifecycle "
                    "hooks; write sidecars after the hook has returned"
                )
            sidecar_descriptor.snapshot(self).write(value)
            return
        snapshot = getattr(self, "snapshot", None)
        should_track = (
            not name.startswith("_")
            and name != "snapshot"
            and not getattr(self, "_snapclass_initializing", False)
            and snapshot is not None
        )
        if (
            should_track
            and _auto_enabled(snapshot._config, self)
            and not getattr(self, "_snapclass_loading", False)
            and snapshot.exists
            and snapshot.modified
        ):
            snapshot.load()
        original_setattr(self, name, value)
        if name.startswith("_") or name == "snapshot":
            return
        if getattr(self, "_snapclass_initializing", False):
            return
        if snapshot is not None:
            if isinstance(value, list):
                object.__setattr__(self, name, _track_value(value, snapshot))
            elif isinstance(value, dict):
                object.__setattr__(self, name, _track_value(value, snapshot))
            elif dataclasses.is_dataclass(value) and not isinstance(value, type):
                _wrap_dataclass_mutables(value, snapshot, set())
            if snapshot._config.infer and name not in {
                field.name for field in dataclasses.fields(self)
            }:
                inferred = set(getattr(self, _INFERRED_FIELDS_ATTR, set()))
                inferred.add(name)
                object.__setattr__(self, _INFERRED_FIELDS_ATTR, inferred)
                inferred_hints = dict(getattr(self, _INFERRED_HINTS_ATTR, {}))
                if name in inferred_hints and not isinstance(value, (list, dict)):
                    coerced = _coerce(value, inferred_hints[name], name, snapshot.stash)
                    object.__setattr__(self, name, coerced)
                elif name not in inferred_hints:
                    inferred_hint = _infer_hint(value)
                    if inferred_hint is not None:
                        inferred_hints[name] = inferred_hint
                object.__setattr__(self, _INFERRED_HINTS_ATTR, inferred_hints)
            if _auto_enabled(snapshot._config, self):
                snapshot.save()
                snapshot.load()

    cls.__setattr__ = __setattr__

    original_getattribute = getattr(cls, "__getattribute__", object.__getattribute__)

    def __getattribute__(self, name: str) -> Any:
        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        if name.startswith("_") or name in {"snapshot", "Meta", "__class__"}:
            return original_getattribute(self, name)
        snapshot = object.__getattribute__(self, "__dict__").get("snapshot")
        config = snapshot._config if snapshot is not None else object.__getattribute__(self, "__class__").__snapclass_config__
        field_names = {field.name for field in dataclasses.fields(self)}
        if (
            name in field_names
            and snapshot is not None
            and _auto_enabled(config, self)
            and not object.__getattribute__(self, "__dict__").get("_snapclass_initializing", False)
            and not object.__getattribute__(self, "__dict__").get("_snapclass_loading", False)
            and snapshot.exists
            and snapshot.modified
        ):
            snapshot.load()
        return original_getattribute(self, name)

    cls.__getattribute__ = __getattribute__


def _install_object_file_methods(cls: type) -> None:
    if not hasattr(cls, "save"):

        def save(
            self: object,
            path: str | os.PathLike[str] | None = None,
            *,
            include_default_values: bool | None = None,
        ) -> object:
            __tracebackhide__ = sessions.HIDDEN_TRACEBACK
            self.snapshot.save(path, include_default_values=include_default_values)
            return self

        cls.save = save

    if not hasattr(cls, "load"):

        def load(self: object, path: str | os.PathLike[str] | None = None) -> object:
            __tracebackhide__ = sessions.HIDDEN_TRACEBACK
            self.snapshot.load(path)
            return self

        cls.load = load


class Snapshot:
    def __init__(
        self,
        instance: object,
        config: Config | None = None,
        stash: Stash | None = None,
        *,
        fields: dict[str, Any] | None = None,
        pattern: str | None = None,
        manual: bool | None = None,
        defaults: bool | None = None,
        infer: bool | None = None,
        minimal_diffs: bool | None = None,
        write_delay: float | None = None,
        root: "Snapshot | None" = None,
    ) -> None:
        self._instance = instance
        if config is None:
            config = Config(
                pattern=pattern,
                module_dir=_module_dir_for(instance.__class__),
                manual=False if manual is None else manual,
                defaults=False if defaults is None else defaults,
                infer=False if infer is None else infer,
                fields=fields or {},
                minimal_diffs=minimal_diffs,
                write_delay=write_delay,
            )
            config.type_hints = _safe_type_hints(instance.__class__)
        self._config = config
        self._stash = stash
        self._root = root
        self._path_override: Path | None = None
        self._last_text: str | None = None
        self._last_mtime: float | None = None
        self._loaded_data: dict[str, Any] | None = None
        self._loaded_path: Path | None = None
        self._ready = False

    @property
    def classname(self) -> str:
        return self._instance.__class__.__name__

    @property
    def defaults(self) -> bool:
        return self._config.defaults

    @defaults.setter
    def defaults(self, value: bool) -> None:
        self._config.defaults = value

    @property
    def _pattern(self) -> str | None:
        return self._config.pattern

    @_pattern.setter
    def _pattern(self, value: str | None) -> None:
        self._config.pattern = value
        self._path_override = None

    @property
    def path(self) -> Path | None:
        if self._path_override is not None:
            return self._path_override
        if not self._config.pattern:
            return None
        formatted = self._config.pattern.format(self=_FormatProxy(self._instance))
        path = Path(formatted)
        if path.is_absolute():
            return path
        if _is_home_relative(path):
            return path.expanduser().resolve()
        stash = self._stash or self._config.stash
        _reject_relative_traversal(path, "snapshot pattern")
        if stash is not None:
            return stash.path / path
        if self._config.pattern.startswith("./"):
            return path.resolve()
        root = self._config.module_dir or Path.cwd()
        return (root / path).resolve()

    @path.setter
    def path(self, value: str | os.PathLike[str]) -> None:
        self._path_override = Path(value)

    @property
    def relpath(self) -> Path | None:
        path = self.path
        if path is None:
            return None
        try:
            return Path(os.path.relpath(path, Path.cwd()))
        except ValueError:
            return path

    @property
    def exists(self) -> bool:
        path = self.path
        return path.exists() if path is not None else False

    @property
    def modified(self) -> bool:
        if not self.exists:
            return True
        if self._last_mtime is None:
            return True
        if self.path.stat().st_mtime != self._last_mtime:
            return True
        if self._last_text is None:
            return False
        return self.path.read_text(encoding="utf-8") != self._last_text

    @modified.setter
    def modified(self, modified: bool) -> None:
        if modified:
            self._last_mtime = None
        else:
            self._last_mtime = self._require_path().stat().st_mtime

    @property
    def manual(self) -> bool:
        if self._root is not None:
            return self._root.manual
        return self._config.manual

    @property
    def infer(self) -> bool:
        if self._root is not None:
            return self._root.infer
        return self._config.infer

    @property
    def stash(self) -> Stash | None:
        return self._stash or self._config.stash

    @property
    def fields(self) -> dict[str, Any]:
        if self._config.fields is not None:
            return self._config.fields
        return _inferred_serializer_fields(self._instance, self._config, self.stash)

    @fields.setter
    def fields(self, value: dict[str, Any]) -> None:
        self._config.fields = value

    @property
    def data(self) -> dict[str, Any]:
        return _to_data(self._instance, self._config, stash=self.stash)

    @property
    def text(self) -> str:
        data = self.data
        if not data:
            return ""
        return _dump_data(self.path or Path(""), data, self._config, self.stash)

    @text.setter
    def text(self, value: str) -> None:
        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        path = self._require_path()
        with _write_lock_for(path):
            self._check_write_conflict(path)
            _write_text_atomic(
                path,
                value,
                write_delay=_effective_write_delay(self._config, self.stash),
            )
        self.load()

    def save(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        include_default_values: bool | None = None,
    ) -> None:
        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        if path is not None:
            self.path = path
        current_path = self._require_path()
        with _write_lock_for(current_path):
            self._check_write_conflict(current_path)
            sidecar.reconcile_before_save(self._instance, current_path)
            data = _to_data(
                self._instance,
                self._config,
                include_default_values,
                stash=self.stash,
            )
            template = self._loaded_data if self._loaded_path == current_path else None
            rendered_data = _data_for_dump(template, data)
            text = _dump_data(current_path, rendered_data, self._config, self.stash)
            _write_text_atomic(
                current_path,
                text,
                write_delay=_effective_write_delay(self._config, self.stash),
            )
        self._loaded_data = rendered_data
        self._loaded_path = current_path
        self._last_text = text
        self.modified = False

    def load(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        _initial: bool = False,
    ) -> None:
        __tracebackhide__ = sessions.HIDDEN_TRACEBACK
        if path is not None:
            self.path = path
        current_path = self._require_path()
        text = current_path.read_text(encoding="utf-8")
        try:
            data = _load_data(current_path, text, self._config, self.stash)
        except Exception as exc:
            raise SnapclassError(f"Failed to load {current_path}: {exc}") from exc
        object.__setattr__(self._instance, "_snapclass_loading", True)
        try:
            try:
                _apply_data(self._instance, data, preserve_non_default=_initial)
            except _CoercionError as exc:
                raise SnapclassError(_schema_mismatch_message(current_path, exc)) from exc
            _wrap_mutables(self._instance)
            self._loaded_data = data
            self._loaded_path = current_path
            self._last_text = text
            self.modified = False
            _mark_snapshot_ready(self._instance)
            _mark_snapshot_loaded(self._instance, current_path)
        finally:
            object.__setattr__(self._instance, "_snapclass_loading", False)

    def _require_path(self) -> Path:
        try:
            path = self.path
        except ValueError as exc:
            raise ValueError(
                f"Unable to resolve path for {self._instance.__class__.__name__}: {exc}"
            ) from exc
        if path is None:
            raise RuntimeError("'pattern' must be set")
        return path

    def _check_write_conflict(self, path: Path) -> None:
        if self._config.conflict != "raise" or not path.exists():
            return
        if self._last_mtime is None:
            raise SnapclassError(
                f"Refusing to overwrite existing file with unloaded data: {path}"
            )
        if path.stat().st_mtime != self._last_mtime:
            raise SnapclassError(
                f"Refusing to overwrite externally modified file: {path}"
            )
        if self._last_text is not None and path.read_text(encoding="utf-8") != self._last_text:
            raise SnapclassError(
                f"Refusing to overwrite externally modified file: {path}"
            )


_absent = object()


class TrackedList(list):
    def __init__(self, values: list[Any], snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        super().__init__(_track_value(value, snapshot) for value in values)

    def _save(self) -> None:
        if _auto_enabled(self._snapshot._config, self._snapshot._instance):
            self._snapshot.save()
            _coerce_tracked_container_in_place(self, self._snapshot)

    def append(self, item: Any) -> None:
        super().append(_track_value(item, self._snapshot))
        self._save()

    def extend(self, values: Any) -> None:
        super().extend(_track_value(value, self._snapshot) for value in values)
        self._save()

    def insert(self, index: int, item: Any) -> None:
        super().insert(index, _track_value(item, self._snapshot))
        self._save()

    def __setitem__(self, index: Any, item: Any) -> None:
        if isinstance(index, slice):
            item = [_track_value(value, self._snapshot) for value in item]
        else:
            item = _track_value(item, self._snapshot)
        super().__setitem__(index, item)
        self._save()

    def __delitem__(self, index: Any) -> None:
        super().__delitem__(index)
        self._save()

    def __iadd__(self, values: Any):
        result = super().__iadd__([_track_value(value, self._snapshot) for value in values])
        self._save()
        return result

    def pop(self, index: int = -1) -> Any:
        value = super().pop(index)
        self._save()
        return value

    def remove(self, item: Any) -> None:
        super().remove(item)
        self._save()

    def clear(self) -> None:
        super().clear()
        self._save()

    def reverse(self) -> None:
        super().reverse()
        self._save()

    def sort(self, *args: Any, **kwargs: Any) -> None:
        super().sort(*args, **kwargs)
        self._save()


class TrackedDeque(deque):
    def __init__(self, values: Any, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        super().__init__(_track_value(value, snapshot) for value in values)

    def _save(self) -> None:
        if _auto_enabled(self._snapshot._config, self._snapshot._instance):
            self._snapshot.save()
            _coerce_tracked_container_in_place(self, self._snapshot)

    def append(self, item: Any) -> None:
        super().append(_track_value(item, self._snapshot))
        self._save()

    def appendleft(self, item: Any) -> None:
        super().appendleft(_track_value(item, self._snapshot))
        self._save()

    def extend(self, values: Any) -> None:
        super().extend(_track_value(value, self._snapshot) for value in values)
        self._save()

    def extendleft(self, values: Any) -> None:
        super().extendleft(_track_value(value, self._snapshot) for value in values)
        self._save()

    def insert(self, index: int, item: Any) -> None:
        super().insert(index, _track_value(item, self._snapshot))
        self._save()

    def __setitem__(self, index: Any, item: Any) -> None:
        if isinstance(index, slice):
            item = [_track_value(value, self._snapshot) for value in item]
        else:
            item = _track_value(item, self._snapshot)
        super().__setitem__(index, item)
        self._save()

    def __delitem__(self, index: Any) -> None:
        super().__delitem__(index)
        self._save()

    def pop(self) -> Any:
        value = super().pop()
        self._save()
        return value

    def popleft(self) -> Any:
        value = super().popleft()
        self._save()
        return value

    def remove(self, item: Any) -> None:
        super().remove(item)
        self._save()

    def clear(self) -> None:
        super().clear()
        self._save()

    def reverse(self) -> None:
        super().reverse()
        self._save()

    def rotate(self, n: int = 1) -> None:
        super().rotate(n)
        self._save()


class TrackedCounter(Counter):
    def __init__(self, values: Any, snapshot: Snapshot) -> None:
        object.__setattr__(self, "_snapshot", snapshot)
        dict.__init__(self)
        Counter.update(self, values)

    def _save(self) -> None:
        if _auto_enabled(self._snapshot._config, self._snapshot._instance):
            self._snapshot.save()
            _coerce_tracked_container_in_place(self, self._snapshot)

    def update(self, *args: Any, **kwargs: Any) -> None:
        super().update(*args, **kwargs)
        self._save()

    def subtract(self, *args: Any, **kwargs: Any) -> None:
        super().subtract(*args, **kwargs)
        self._save()

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        self._save()

    def __delitem__(self, key: Any) -> None:
        super().__delitem__(key)
        self._save()

    def pop(self, key: Any, default: Any = _absent) -> Any:
        if default is _absent:
            value = super().pop(key)
        else:
            value = super().pop(key, default)
        self._save()
        return value

    def popitem(self) -> tuple[Any, Any]:
        value = super().popitem()
        self._save()
        return value

    def clear(self) -> None:
        super().clear()
        self._save()


class TrackedDict(dict):
    def __init__(self, values: dict[Any, Any], snapshot: Snapshot) -> None:
        object.__setattr__(self, "_snapshot", snapshot)
        super().__init__({key: _track_value(value, snapshot) for key, value in values.items()})

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self[name] = value

    def __delattr__(self, name: str) -> None:
        if name.startswith("_"):
            object.__delattr__(self, name)
            return
        try:
            del self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def _save(self) -> None:
        if _auto_enabled(self._snapshot._config, self._snapshot._instance):
            self._snapshot.save()
            _coerce_tracked_container_in_place(self, self._snapshot)

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, _track_value(value, self._snapshot))
        self._save()

    def __delitem__(self, key: Any) -> None:
        super().__delitem__(key)
        self._save()

    def update(self, *args: Any, **kwargs: Any) -> None:
        values = dict(*args, **kwargs)
        super().update({key: _track_value(value, self._snapshot) for key, value in values.items()})
        self._save()

    def pop(self, key: Any, default: Any = _absent) -> Any:
        if default is _absent:
            value = super().pop(key)
        else:
            value = super().pop(key, default)
        self._save()
        return value

    def popitem(self) -> tuple[Any, Any]:
        value = super().popitem()
        self._save()
        return value

    def clear(self) -> None:
        super().clear()
        self._save()

    def setdefault(self, key: Any, default: Any = None) -> Any:
        existed = key in self
        value = super().setdefault(key, _track_value(default, self._snapshot))
        if not existed:
            self._save()
        return value


class TrackedDefaultDict(defaultdict):
    def __init__(
        self,
        default_factory: Callable[[], Any] | None,
        values: Mapping[Any, Any],
        snapshot: Snapshot,
    ) -> None:
        object.__setattr__(self, "_snapshot", snapshot)
        super().__init__(default_factory)
        dict.update(
            self,
            {key: _track_value(value, snapshot) for key, value in values.items()},
        )

    def __missing__(self, key: Any) -> Any:
        if self.default_factory is None:
            raise KeyError(key)
        value = _track_value(self.default_factory(), self._snapshot)
        dict.__setitem__(self, key, value)
        self._save()
        return value

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self[name] = value

    def __delattr__(self, name: str) -> None:
        if name.startswith("_"):
            object.__delattr__(self, name)
            return
        try:
            del self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def _save(self) -> None:
        if _auto_enabled(self._snapshot._config, self._snapshot._instance):
            self._snapshot.save()
            _coerce_tracked_container_in_place(self, self._snapshot)

    def __setitem__(self, key: Any, value: Any) -> None:
        dict.__setitem__(self, key, _track_value(value, self._snapshot))
        self._save()

    def __delitem__(self, key: Any) -> None:
        dict.__delitem__(self, key)
        self._save()

    def update(self, *args: Any, **kwargs: Any) -> None:
        values = dict(*args, **kwargs)
        dict.update(
            self,
            {key: _track_value(value, self._snapshot) for key, value in values.items()},
        )
        self._save()

    def pop(self, key: Any, default: Any = _absent) -> Any:
        if default is _absent:
            value = dict.pop(self, key)
        else:
            value = dict.pop(self, key, default)
        self._save()
        return value

    def popitem(self) -> tuple[Any, Any]:
        value = dict.popitem(self)
        self._save()
        return value

    def clear(self) -> None:
        dict.clear(self)
        self._save()

    def setdefault(self, key: Any, default: Any = None) -> Any:
        existed = key in self
        value = dict.setdefault(self, key, _track_value(default, self._snapshot))
        if not existed:
            self._save()
        return value


def _track_value(value: Any, snapshot: Snapshot) -> Any:
    return _track_value_inner(value, snapshot, set())


def _track_value_inner(value: Any, snapshot: Snapshot, seen: set[int]) -> Any:
    if isinstance(value, TrackedList):
        if value._snapshot is snapshot:
            return value
        return TrackedList(list(value), snapshot)
    if isinstance(value, TrackedDeque):
        if value._snapshot is snapshot:
            return value
        return TrackedDeque(value, snapshot)
    if isinstance(value, TrackedCounter):
        if value._snapshot is snapshot:
            return value
        return TrackedCounter(value, snapshot)
    if isinstance(value, TrackedDict):
        if value._snapshot is snapshot:
            return value
        return TrackedDict(dict(value), snapshot)
    if isinstance(value, TrackedDefaultDict):
        if value._snapshot is snapshot:
            return value
        return TrackedDefaultDict(value.default_factory, value, snapshot)
    if isinstance(value, list):
        return TrackedList(value, snapshot)
    if isinstance(value, deque):
        return TrackedDeque(value, snapshot)
    if isinstance(value, Counter):
        return TrackedCounter(value, snapshot)
    if isinstance(value, defaultdict):
        return TrackedDefaultDict(value.default_factory, value, snapshot)
    if isinstance(value, dict):
        return TrackedDict(value, snapshot)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        _wrap_dataclass_mutables(value, snapshot, seen)
    return value


def _wrap_dataclass_mutables(instance: object, snapshot: Snapshot, seen: set[int]) -> None:
    ident = id(instance)
    if ident in seen:
        return
    seen.add(ident)
    for field in dataclasses.fields(instance):
        if not field.init:
            continue
        try:
            value = getattr(instance, field.name)
        except AttributeError:
            continue
        tracked = _track_value_inner(value, snapshot, seen)
        if tracked is not value:
            object.__setattr__(instance, field.name, tracked)


def _coerce_tracked_container_in_place(
    container: TrackedList
    | TrackedDeque
    | TrackedCounter
    | TrackedDefaultDict
    | TrackedDict,
    snapshot: Snapshot,
) -> None:
    hint = _hint_for_tracked_value(snapshot._instance, container, None, set())
    if hint is None:
        return
    coerced = _coerce_for_preserialization(
        container,
        hint,
        "value",
        snapshot.stash,
        _effective_minimal_diffs(snapshot._config, snapshot.stash),
    )
    if isinstance(container, TrackedList) and isinstance(coerced, list):
        items = list(coerced)
        list.clear(container)
        list.extend(container, (_track_value(item, snapshot) for item in items))
    elif isinstance(container, TrackedDeque) and isinstance(coerced, list):
        items = list(coerced)
        deque.clear(container)
        deque.extend(container, (_track_value(item, snapshot) for item in items))
    elif isinstance(container, TrackedCounter) and isinstance(coerced, dict):
        items = dict(coerced)
        dict.clear(container)
        dict.update(container, items)
    elif isinstance(container, (TrackedDefaultDict, TrackedDict)) and isinstance(
        coerced,
        dict,
    ):
        items = dict(coerced)
        dict.clear(container)
        dict.update(
            container,
            {key: _track_value(value, snapshot) for key, value in items.items()},
        )


def _hint_for_tracked_value(value: Any, target: object, hint: Any, seen: set[int]) -> Any:
    if value is target:
        return hint
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return None
    ident = id(value)
    if ident in seen:
        return None
    seen.add(ident)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        hints = _dataclass_type_hints(value.__class__)
        for field in dataclasses.fields(value):
            if not field.init:
                continue
            try:
                child = getattr(value, field.name)
            except AttributeError:
                continue
            found = _hint_for_tracked_value(child, target, hints.get(field.name), seen)
            if found is not None:
                return found
    if hasattr(value, "snapshot"):
        for name, inferred_hint in _inferred_hints(value).items():
            try:
                child = getattr(value, name)
            except AttributeError:
                continue
            found = _hint_for_tracked_value(child, target, inferred_hint, seen)
            if found is not None:
                return found
    origin = get_origin(hint)
    args = get_args(hint)
    if isinstance(value, (TrackedDeque, TrackedList, deque, list)):
        item_hint = args[0] if origin in (deque, list) and args else Any
        for item in value:
            found = _hint_for_tracked_value(item, target, item_hint, seen)
            if found is not None:
                return found
    if isinstance(value, (TrackedCounter, Counter)):
        item_hint = args[0] if origin is Counter and args else Any
        for item in value:
            found = _hint_for_tracked_value(item, target, item_hint, seen)
            if found is not None:
                return found
    if isinstance(value, (TrackedDefaultDict, TrackedDict, dict)):
        value_hint = (
            args[1]
            if _is_mapping_origin(origin) and len(args) > 1
            else Any
        )
        for item in value.values():
            found = _hint_for_tracked_value(item, target, value_hint, seen)
            if found is not None:
                return found
    return None


def _attach_snapshot(instance: object, config: Config, stash: Stash | None = None) -> None:
    object.__setattr__(instance, "snapshot", Snapshot(instance, config, stash))
    _wrap_mutables(instance)


def _wrap_mutables(instance: object) -> None:
    snapshot = getattr(instance, "snapshot", None)
    if snapshot is None:
        return
    for name in _tracked_attribute_names(instance):
        value = getattr(instance, name, None)
        if isinstance(value, list) and not isinstance(value, TrackedList):
            object.__setattr__(instance, name, _track_value(value, snapshot))
        elif isinstance(value, deque) and not isinstance(value, TrackedDeque):
            object.__setattr__(instance, name, _track_value(value, snapshot))
        elif isinstance(value, Counter) and not isinstance(value, TrackedCounter):
            object.__setattr__(instance, name, _track_value(value, snapshot))
        elif isinstance(value, dict) and not isinstance(
            value,
            (TrackedCounter, TrackedDefaultDict, TrackedDict),
        ):
            object.__setattr__(instance, name, _track_value(value, snapshot))
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            _wrap_dataclass_mutables(value, snapshot, set())


def _to_data(
    instance: object,
    config: Config,
    include_default_values: bool | None = None,
    *,
    stash: Stash | None = None,
) -> dict[str, Any]:
    include_defaults = config.defaults if include_default_values is None else include_default_values
    minimal_diffs = _effective_minimal_diffs(config, stash)
    data: dict[str, Any] = {}
    if config.unknown == "preserve":
        data.update(_preserved_unknown_data(instance, minimal_diffs))
    pattern = config.pattern or ""
    for field in dataclasses.fields(instance):
        if not field.init or _placeholder_for(field.name) in pattern:
            continue
        if config.fields is not None and field.name not in config.fields:
            continue
        value = getattr(instance, field.name, Missing)
        if _is_missing(value):
            continue
        if not include_defaults and value == _field_default(
            field, _default_cache_for(instance)
        ):
            continue
        serializer = _field_serializer(config, field.name, stash)
        if serializer is not None:
            data[field.name] = _to_preserialization_value(
                serializer,
                value,
                instance,
                minimal_diffs=minimal_diffs,
            )
        else:
            data[field.name] = _plain(
                _coerce_for_preserialization(
                    value,
                    config.type_hints.get(field.name),
                    field.name,
                    stash,
                    minimal_diffs,
                ),
                minimal_diffs,
            )
    if config.infer:
        for name in _inferred_attr_names(instance):
            if name in data or _placeholder_for(name) in pattern:
                continue
            value = getattr(instance, name, Missing)
            if not _is_missing(value):
                data[name] = _plain(value, minimal_diffs)
    return data


def _plain(value: Any, minimal_diffs: bool | None = None) -> Any:
    if minimal_diffs is None:
        minimal_diffs = sessions.MINIMAL_DIFFS
    if isinstance(value, (TrackedDeque, TrackedList, deque, list)):
        if not value and minimal_diffs:
            return [None]
        return [_plain(item, minimal_diffs) for item in value]
    if isinstance(value, (TrackedDict, dict)):
        return {key: _plain(item, minimal_diffs) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_plain(item, minimal_diffs) for item in sorted(value, key=_sort_key)]
    if dataclasses.is_dataclass(value):
        return {
            f.name: _plain(getattr(value, f.name), minimal_diffs)
            for f in dataclasses.fields(value)
            if f.init
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, _datetime.datetime):
        return value.isoformat()
    if isinstance(value, _datetime.date):
        return value.isoformat()
    if isinstance(value, Path):
        return os.fspath(value)
    return value


def _coerce_for_preserialization(
    value: Any,
    hint: Any,
    field_path: str,
    stash: Stash | None = None,
    minimal_diffs: bool | None = None,
) -> Any:
    hint = _normalize_string_hint(hint)
    origin = get_origin(hint)
    args = get_args(hint)
    if value is None:
        return _coerce(value, hint, field_path, stash)
    if origin in (Union, types.UnionType):
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1:
            return _coerce_for_preserialization(
                value,
                non_none[0],
                field_path,
                stash,
                minimal_diffs,
            )
        return _coerce(value, hint, field_path, stash)
    if origin is list:
        subtype = args[0] if args else Any
        return [
            _coerce_for_preserialization(
                item,
                subtype,
                f"{field_path}[{index}]",
                stash,
                minimal_diffs,
            )
            for index, item in enumerate(_list_values(value))
        ]
    if origin is deque:
        subtype = args[0] if args else Any
        return [
            _coerce_for_preserialization(
                item,
                subtype,
                f"{field_path}[{index}]",
                stash,
                minimal_diffs,
            )
            for index, item in enumerate(_list_values(value))
        ]
    if origin in (set, frozenset, AbstractSet):
        subtype = args[0] if args else Any
        values = {
            _coerce_for_preserialization(
                item,
                subtype,
                f"{field_path}[{index}]",
                stash,
                minimal_diffs,
            )
            for index, item in enumerate(value)
        }
        return frozenset(values) if origin is frozenset else values
    if origin is Counter:
        key_type = args[0] if args else Any
        return {
            _coerce_for_preserialization(
                key,
                key_type,
                f"{field_path}.<key>",
                stash,
                minimal_diffs,
            ): int(item)
            for key, item in dict(value).items()
        }
    if _is_mapping_origin(origin):
        key_type = args[0] if args else Any
        value_type = args[1] if len(args) > 1 else Any
        return {
            _coerce_for_preserialization(
                key,
                key_type,
                f"{field_path}.<key>",
                stash,
                minimal_diffs,
            ):
            _coerce_for_preserialization(
                item,
                value_type,
                f"{field_path}[{key!r}]",
                stash,
                minimal_diffs,
            )
            for key, item in dict(value).items()
        }
    if dataclasses.is_dataclass(hint) and dataclasses.is_dataclass(value):
        nested_hints = _dataclass_type_hints(hint)
        return {
            field.name: _coerce_for_preserialization(
                getattr(value, field.name),
                nested_hints.get(field.name),
                f"{field_path}.{field.name}",
                stash,
                minimal_diffs,
            )
            for field in dataclasses.fields(value)
            if field.init
        }
    serializer = serializers.serializer_for_hint(
        hint,
        serializers=_effective_serializers(stash),
    )
    if serializer is not None:
        return _to_preserialization_value(
            serializer,
            value,
            None,
            minimal_diffs=minimal_diffs,
        )
    return _coerce(value, hint, field_path, stash)


def _infer_hint(value: Any) -> Any:
    if isinstance(value, list):
        item_hint = _infer_homogeneous_hint(value)
        return list[item_hint] if item_hint is not None else list
    if isinstance(value, dict):
        return dict
    if isinstance(value, bool):
        return bool
    if isinstance(value, int) and not isinstance(value, bool):
        return int
    if isinstance(value, float):
        return float
    if isinstance(value, str):
        return str
    return None


def _infer_homogeneous_hint(values: list[Any]) -> Any:
    hints = {_infer_hint(value) for value in values}
    hints.discard(None)
    if len(hints) == 1:
        return next(iter(hints))
    return None


def _effective_formatters(stash: Stash | None) -> dict[str, type[FileFormatter]]:
    return stash.effective_formatters() if stash is not None else {}


def _effective_serializers(
    stash: Stash | None,
) -> dict[type | str, type[serializers.Serializer]]:
    return stash.effective_serializers() if stash is not None else {}


def _effective_minimal_diffs(config: Config | None, stash: Stash | None) -> bool:
    if config is not None and config.minimal_diffs is not None:
        return config.minimal_diffs
    if stash is not None:
        value = stash.effective_minimal_diffs()
        if value is not None:
            return value
    return sessions.MINIMAL_DIFFS


def _effective_write_delay(config: Config | None, stash: Stash | None) -> float:
    if config is not None and config.write_delay is not None:
        return config.write_delay
    if stash is not None:
        value = stash.effective_write_delay()
        if value is not None:
            return value
    return sessions.WRITE_DELAY


def _load_data(path: Path, text: str, config: Config, stash: Stash | None) -> dict[str, Any]:
    formatter = formatters.formatter_for(
        path,
        config.formatter,
        formatters=_effective_formatters(stash),
    )
    loads_path = getattr(formatter, "loads_path", None)
    if loads_path is not None:
        data = loads_path(path, text)
    else:
        data = formatter.loads(text)
    return _migrate_loaded_data(path, data, config)


def _migrate_loaded_data(path: Path, data: dict[str, Any], config: Config) -> dict[str, Any]:
    if config.migrate is None:
        return data

    working = data.copy()
    try:
        migrated = _call_migrate(config.migrate, working, path)
    except Exception as exc:
        raise SnapclassError(f"Failed to migrate {path}: {exc}") from exc
    if migrated is None:
        migrated = working
    if not isinstance(migrated, Mapping):
        raise SnapclassError(
            f"Failed to migrate {path}: migrate returned "
            f"{type(migrated).__name__}, expected mapping"
        )
    if isinstance(migrated, dict):
        return migrated
    return dict(migrated)


def _call_migrate(
    migrate: Callable[..., Mapping[str, Any] | None],
    data: dict[str, Any],
    path: Path,
) -> Mapping[str, Any] | None:
    try:
        signature = inspect.signature(migrate)
    except (TypeError, ValueError):
        return migrate(data, path=path)

    try:
        signature.bind(data, path=path)
    except TypeError:
        try:
            signature.bind(data, path)
        except TypeError:
            return migrate(data)
        return migrate(data, path)
    else:
        return migrate(data, path=path)


def _dump_data(
    path: Path,
    data: dict[str, Any],
    config: Config,
    stash: Stash | None,
) -> str:
    return formatters.formatter_for(
        path,
        config.formatter,
        formatters=_effective_formatters(stash),
    ).dumps(data)


def _data_for_dump(template: dict[str, Any] | None, data: dict[str, Any]) -> dict[str, Any]:
    if template is None:
        return data
    rendered = copy.deepcopy(template)
    return _merge_dump_data(rendered, data)


def _merge_dump_data(template: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    for key in list(template.keys()):
        if key not in data:
            del template[key]
    for key, value in data.items():
        if key in template:
            template[key] = _merge_dump_value(template[key], value)
        else:
            template[key] = value
    return template


def _merge_dump_value(existing: Any, value: Any) -> Any:
    if isinstance(existing, dict) and isinstance(value, dict):
        return _merge_dump_data(existing, value)
    if isinstance(existing, str) and type(existing) is not str and isinstance(value, str):
        try:
            return type(existing)(value)
        except Exception:
            return value
    return value


def _normalize_unknown_policy(unknown: str, extras_field: str | None) -> str:
    if extras_field is not None and unknown == "ignore":
        unknown = "collect"
    allowed = {"ignore", "preserve", "reject", "collect"}
    if unknown not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"unknown must be one of {choices}; got {unknown!r}")
    if unknown == "collect" and not extras_field:
        raise ValueError("unknown='collect' requires extras_field")
    return unknown


def _normalize_conflict_policy(conflict: str) -> str:
    allowed = {"overwrite", "raise"}
    if conflict not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"conflict must be one of {choices}; got {conflict!r}")
    return conflict


def _validate_extras_field(cls: type, unknown: str, extras_field: str | None) -> None:
    if unknown != "collect":
        return
    field_names = {field.name for field in dataclasses.fields(cls) if field.init}
    if extras_field not in field_names:
        raise ValueError(
            f"extras_field={extras_field!r} must name an init dataclass field "
            f"on {cls.__name__}"
        )


def _preserved_unknown_data(
    instance: object,
    minimal_diffs: bool | None = None,
) -> dict[str, Any]:
    data = getattr(instance, _UNKNOWN_DATA_ATTR, {})
    if not isinstance(data, dict):
        return {}
    known_fields = {field.name for field in dataclasses.fields(instance)}
    return {
        key: _plain(value, minimal_diffs)
        for key, value in data.items()
        if key not in known_fields
    }


def _prepare_data_with_unknowns(
    instance: object, data: dict[str, Any], config: Config
) -> dict[str, Any]:
    field_names = {field.name for field in dataclasses.fields(instance)}
    unknown_data = {key: value for key, value in data.items() if key not in field_names}

    if config.unknown == "preserve":
        object.__setattr__(instance, _UNKNOWN_DATA_ATTR, dict(unknown_data))
    else:
        object.__setattr__(instance, _UNKNOWN_DATA_ATTR, {})
    if config.infer:
        inferred_hints = {
            key: hint
            for key, value in unknown_data.items()
            if (hint := _infer_hint(value)) is not None
        }
        for name in _inferred_attr_names(instance) - set(unknown_data):
            if hasattr(instance, name):
                object.__delattr__(instance, name)
        object.__setattr__(instance, _INFERRED_FIELDS_ATTR, set(unknown_data))
        object.__setattr__(instance, _INFERRED_HINTS_ATTR, inferred_hints)
    else:
        object.__setattr__(instance, _INFERRED_FIELDS_ATTR, set())
        object.__setattr__(instance, _INFERRED_HINTS_ATTR, {})

    if not unknown_data:
        return data

    if config.infer:
        for key, value in unknown_data.items():
            object.__setattr__(instance, key, value)
        return data

    if config.unknown == "reject":
        fields = ", ".join(sorted(str(key) for key in unknown_data))
        raise SnapclassError(
            f"Unknown fields for {instance.__class__.__name__}: {fields}"
        )

    if config.unknown == "collect":
        extras_field = config.extras_field
        if extras_field is None:
            raise SnapclassError("unknown='collect' requires extras_field")
        merged: dict[Any, Any] = {}
        existing = data.get(extras_field)
        if isinstance(existing, dict):
            merged.update(existing)
        merged.update(unknown_data)
        prepared = dict(data)
        prepared[extras_field] = merged
        return prepared

    return data


def _inferred_attr_names(instance: object) -> set[str]:
    names = getattr(instance, _INFERRED_FIELDS_ATTR, set())
    if not isinstance(names, set):
        return set()
    known_fields = {field.name for field in dataclasses.fields(instance)}
    return {name for name in names if name not in known_fields}


def _inferred_hints(instance: object) -> dict[str, Any]:
    hints = getattr(instance, _INFERRED_HINTS_ATTR, {})
    return hints if isinstance(hints, dict) else {}


def _tracked_attribute_names(instance: object) -> list[str]:
    names = [field.name for field in dataclasses.fields(instance)]
    names.extend(sorted(_inferred_attr_names(instance)))
    return names


def _apply_data(
    instance: object, data: dict[str, Any], *, preserve_non_default: bool
) -> None:
    config = _config_for_instance(instance)
    snapshot = getattr(instance, "snapshot", None)
    stash = snapshot.stash if snapshot is not None else config.stash
    hints = config.type_hints
    data = _prepare_data_with_unknowns(instance, data, config)
    for field in dataclasses.fields(instance):
        if not field.init or field.name not in data:
            continue
        if config.fields is not None and field.name not in config.fields:
            continue
        current = getattr(instance, field.name, Missing)
        if (
            preserve_non_default
            and not _is_missing(current)
            and current != _field_default(field, _default_cache_for(instance))
        ):
            continue
        serializer = _field_serializer(config, field.name, stash)
        if serializer is not None:
            try:
                if isinstance(serializer, type) and issubclass(
                    serializer,
                    (serializers.List, serializers.Set, serializers.Dictionary, serializers.Dataclass),
                ):
                    target = None if _is_missing(current) else current
                else:
                    target = instance
                value = _to_python_value(serializer, data[field.name], target)
            except Exception as exc:
                raise _CoercionError(field.name, serializer, data[field.name], exc) from exc
        else:
            value = _coerce(data[field.name], hints.get(field.name), field.name, stash)
            if isinstance(current, TrackedDeque) and isinstance(value, deque):
                deque.clear(current)
                deque.extend(
                    current,
                    (_track_value(item, current._snapshot) for item in value),
                )
                value = current
            elif isinstance(current, TrackedCounter) and isinstance(value, Counter):
                dict.clear(current)
                dict.update(current, value)
                value = current
            elif isinstance(current, TrackedDefaultDict) and isinstance(value, dict):
                dict.clear(current)
                dict.update(
                    current,
                    {
                        key: _track_value(item, current._snapshot)
                        for key, item in value.items()
                    },
                )
                value = current
        object.__setattr__(instance, field.name, value)
    _fill_missing_init_fields(instance, config, stash)


def _fill_missing_init_fields(
    instance: object,
    config: Config,
    stash: Stash | None = None,
) -> None:
    hints = config.type_hints
    pattern = config.pattern or ""
    for field in dataclasses.fields(instance):
        if not field.init or _placeholder_for(field.name) in pattern:
            continue
        if config.fields is not None and field.name not in config.fields:
            continue
        if not _is_missing(getattr(instance, field.name, Missing)):
            continue
        default = _field_default(field, _default_cache_for(instance))
        if _is_missing(default):
            default = _missing_value_for_hint(hints.get(field.name), field.name, stash)
        if not _is_missing(default):
            object.__setattr__(instance, field.name, default)


def _config_for_instance(instance: object) -> Config:
    snapshot = getattr(instance, "snapshot", None)
    if snapshot is not None:
        return snapshot._config
    return instance.__class__.__snapclass_config__


def _field_serializer(
    config: Config,
    name: str,
    stash: Stash | None = None,
) -> type[serializers.Serializer] | None:
    if config.fields and name in config.fields:
        return config.fields[name]
    return serializers.serializer_for_hint(
        config.type_hints.get(name),
        serializers=_effective_serializers(stash),
    )


def _inferred_serializer_fields(
    instance: object,
    config: Config,
    stash: Stash | None = None,
) -> dict[str, Any]:
    pattern = config.pattern or ""
    fields: dict[str, Any] = {}
    for field in dataclasses.fields(instance):
        if not field.init or _placeholder_for(field.name) in pattern:
            continue
        serializer = _attr_serializer_for_hint(config.type_hints.get(field.name), stash)
        if serializer is not None:
            fields[field.name] = serializer
    return fields


def _attr_serializer_for_hint(
    hint: Any,
    stash: Stash | None = None,
) -> type[serializers.Serializer] | None:
    hint = _normalize_string_hint(hint)
    return serializers.serializer_for_hint(
        hint,
        serializers=_effective_serializers(stash),
    )


def _normalize_string_hint(hint: Any) -> Any:
    if not isinstance(hint, str):
        return hint
    normalized = hint.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        normalized = normalized[1:-1]
    return {
        "bool": bool,
        "int": int,
        "float": float,
        "str": str,
        "None": type(None),
        "NoneType": type(None),
    }.get(normalized, normalized)


def _to_python_value(serializer: Any, value: Any, target_object: Any) -> Any:
    method = serializer.to_python_value
    return _call_serializer(method, value, target_object)


def _to_preserialization_value(
    serializer: Any,
    value: Any,
    target_object: Any,
    *,
    minimal_diffs: bool | None = None,
) -> Any:
    method = serializer.to_preserialization_data
    return _call_serializer(
        method,
        value,
        target_object,
        minimal_diffs=minimal_diffs,
    )


def _call_serializer(method: Any, value: Any, target_object: Any, **kwargs: Any) -> Any:
    parameters = inspect.signature(method).parameters
    optional_kwargs = {
        name: value
        for name, value in kwargs.items()
        if value is not None and name in parameters
    }
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        extra_kwargs = {key: item for key, item in kwargs.items() if item is not None}
        return method(value, target_object=target_object, **extra_kwargs)
    if "target_object" in parameters:
        return method(value, target_object=target_object, **optional_kwargs)
    return method(value, **optional_kwargs)


def _coerce(
    value: Any,
    hint: Any,
    field_path: str = "value",
    stash: Stash | None = None,
) -> Any:
    hint = _normalize_string_hint(hint)
    origin = get_origin(hint)
    args = get_args(hint)
    if value is None:
        if _hint_allows_none(hint):
            return None
        if origin is list:
            return []
        if origin is deque:
            return deque()
        if origin is Counter:
            return Counter()
        if hint is bool:
            return False
        if hint is int:
            return 0
        if hint is float:
            return 0.0
        if hint is str:
            return ""
        return None
    if origin in (Union, types.UnionType):
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1:
            return _coerce(value, non_none[0], field_path, stash)
        return _coerce_union(value, non_none, field_path, stash)
    if origin is list:
        subtype = args[0] if args else Any
        value = _list_values(value)
        return [
            _coerce(item, subtype, f"{field_path}[{index}]", stash)
            for index, item in enumerate(value)
        ]
    if origin is deque:
        subtype = args[0] if args else Any
        value = _list_values(value)
        return deque(
            _coerce(item, subtype, f"{field_path}[{index}]", stash)
            for index, item in enumerate(value)
        )
    if origin in (set, frozenset, AbstractSet):
        subtype = args[0] if args else Any
        values = {
            _coerce(item, subtype, f"{field_path}[{index}]", stash)
            for index, item in enumerate(value)
        }
        return frozenset(values) if origin is frozenset else values
    if origin is Counter:
        key_type = args[0] if args else Any
        return Counter(
            {
                _coerce(key, key_type, f"{field_path}.<key>", stash): int(item)
                for key, item in dict(value).items()
            }
        )
    if _is_mapping_origin(origin):
        key_type = args[0] if args else Any
        value_type = args[1] if len(args) > 1 else Any
        return {
            _coerce(key, key_type, f"{field_path}.<key>", stash): _coerce(
                item, value_type, f"{field_path}[{key!r}]", stash
            )
            for key, item in dict(value).items()
        }
    serializer = serializers.serializer_for_hint(
        hint,
        serializers=_effective_serializers(stash),
    )
    if serializer is not None:
        try:
            return _to_python_value(serializer, value, None)
        except Exception as exc:
            raise _CoercionError(field_path, serializer, value, exc) from exc
    if _is_typed_dict_hint(hint):
        warnings.warn(
            f"TypedDict annotation {_hint_name(hint)} is treated as a plain dictionary",
            RuntimeWarning,
            stacklevel=2,
        )
        return _convert_or_raise(lambda: dict(value), field_path, hint, value)
    if dataclasses.is_dataclass(hint) and isinstance(value, dict):
        return _coerce_dataclass_mapping(hint, value, field_path, stash)
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        if isinstance(value, hint):
            return value
        try:
            return hint(value)
        except ValueError as exc:
            try:
                return hint[str(value)]
            except Exception as name_exc:
                raise _CoercionError(field_path, hint, value, name_exc) from exc
    if hint is _datetime.datetime:
        if isinstance(value, _datetime.datetime):
            return value
        return _convert_or_raise(
            lambda: _datetime.datetime.fromisoformat(str(value)),
            field_path,
            hint,
            value,
        )
    if hint is _datetime.date:
        if isinstance(value, _datetime.datetime):
            return value.date()
        if isinstance(value, _datetime.date):
            return value
        return _convert_or_raise(
            lambda: _datetime.date.fromisoformat(str(value)),
            field_path,
            hint,
            value,
        )
    if hint is Path:
        return value if isinstance(value, Path) else Path(value)
    if hint in (str, int, float, bool):
        return _convert_or_raise(lambda: hint(value), field_path, hint, value)
    return value


def _convert_or_raise(convert: Callable[[], Any], field_path: str, hint: Any, value: Any) -> Any:
    try:
        return convert()
    except Exception as exc:
        raise _CoercionError(field_path, hint, value, exc) from exc


def _schema_mismatch_message(path: Path, error: _CoercionError) -> str:
    return (
        f"Schema mismatch loading {path}: field {error.field_path!r} expected "
        f"{_hint_name(error.hint)}, received {_received_name(error.value)} "
        f"{error.value!r}: {error}"
    )


def _hint_name(hint: Any) -> str:
    hint = _normalize_string_hint(hint)
    if hint is None:
        return "Any"
    if isinstance(hint, type):
        return hint.__name__
    return str(hint)


def _received_name(value: Any) -> str:
    return type(value).__name__


def _coerce_union(
    value: Any,
    hints: list[Any],
    field_path: str,
    stash: Stash | None = None,
) -> Any:
    for hint in hints:
        if _matches_hint(value, hint):
            return value
    errors: list[Exception] = []
    for hint in hints:
        try:
            return _coerce(value, hint, field_path, stash)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise _CoercionError(field_path, Union[tuple(hints)], value, errors[-1])
    return value


def _matches_hint(value: Any, hint: Any) -> bool:
    hint = _normalize_string_hint(hint)
    origin = get_origin(hint)
    if _is_typed_dict_hint(hint):
        return isinstance(value, dict)
    if origin is None:
        return isinstance(hint, type) and type(value) is hint
    if origin is list:
        return isinstance(value, list)
    if origin is deque:
        return isinstance(value, deque)
    if origin is set:
        return isinstance(value, set)
    if origin is frozenset:
        return isinstance(value, frozenset)
    if origin is AbstractSet:
        return isinstance(value, set)
    if origin is Counter:
        return isinstance(value, Counter)
    if _is_mapping_origin(origin):
        return isinstance(value, dict)
    if dataclasses.is_dataclass(hint):
        return isinstance(value, hint)
    return False


def _coerce_dataclass_mapping(
    hint: type,
    value: dict[str, Any],
    field_path: str,
    stash: Stash | None = None,
) -> Any:
    nested_hints = _dataclass_type_hints(hint)
    kwargs: dict[str, Any] = {}
    for field in dataclasses.fields(hint):
        if not field.init:
            continue
        child_path = f"{field_path}.{field.name}"
        child_hint = nested_hints.get(field.name)
        if field.name in value:
            kwargs[field.name] = _coerce(value[field.name], child_hint, child_path, stash)
            continue
        default = _field_default(field)
        if _is_missing(default):
            default = _missing_value_for_hint(child_hint, child_path, stash)
        if not _is_missing(default):
            kwargs[field.name] = default
    return hint(**kwargs)


def _missing_value_for_hint(
    hint: Any,
    field_path: str,
    stash: Stash | None = None,
) -> Any:
    hint = _normalize_string_hint(hint)
    origin = get_origin(hint)
    args = get_args(hint)
    if _hint_allows_none(hint):
        return None
    if origin in (Union, types.UnionType):
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1:
            return _missing_value_for_hint(non_none[0], field_path, stash)
        return Missing
    if origin is list:
        return []
    if origin is deque:
        return deque()
    if origin in (set, AbstractSet):
        return set()
    if origin is frozenset:
        return frozenset()
    if origin is Counter:
        return Counter()
    if _is_mapping_origin(origin):
        return {}
    serializer = _attr_serializer_for_hint(hint, stash)
    if serializer is not None and getattr(serializer, "DEFAULT", None) is not None:
        return _to_python_value(serializer, None, None)
    if dataclasses.is_dataclass(hint):
        return _convert_or_raise(
            lambda: _coerce_dataclass_mapping(hint, {}, field_path, stash),
            field_path,
            hint,
            {},
        )
    return Missing


def _hint_allows_none(hint: Any) -> bool:
    origin = get_origin(hint)
    if origin not in (Union, types.UnionType):
        return False
    return any(arg is type(None) for arg in get_args(hint))


def _list_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if value == [None]:
        return []
    if isinstance(value, str):
        if "," in value:
            return [item.strip() for item in value.split(",") if item.strip()]
        return [value]
    if isinstance(value, (deque, list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _is_typed_dict_hint(hint: Any) -> bool:
    try:
        if is_typeddict(hint):
            return True
    except TypeError:
        return False
    return hasattr(hint, "__required_keys__") and hasattr(hint, "__total__")


def _is_mapping_origin(origin: Any) -> bool:
    return origin in (dict, Mapping, MutableMapping) or (
        isinstance(origin, type) and issubclass(origin, Mapping)
    )


def _sort_key(value: Any) -> tuple[str, str]:
    plain = _plain(value)
    return (type(plain).__name__, repr(plain))


def _field_default(
    field: dataclasses.Field,
    default_cache: dict[str, Any] | None = None,
) -> Any:
    if field.default is not dataclasses.MISSING:
        return field.default
    if field.default_factory is not dataclasses.MISSING:  # type: ignore
        if default_cache is not None:
            if field.name not in default_cache:
                default_cache[field.name] = field.default_factory()  # type: ignore
            return default_cache[field.name]
        return field.default_factory()  # type: ignore
    return Missing


def _default_cache_for(instance: object) -> dict[str, Any]:
    cache = getattr(instance, _DEFAULT_CACHE_ATTR, None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    object.__setattr__(instance, _DEFAULT_CACHE_ATTR, cache)
    return cache


def _placeholder_for(name: str) -> str:
    return "{self." + name + "}"


def _coerce_stash(value: Stash | str | os.PathLike[str]) -> Stash:
    return value if isinstance(value, Stash) else Stash(value)


def _has_path_value(values: list[str]) -> bool:
    return any("/" in value or "\\" in value for value in values)


class _PatternMatcher:
    def __init__(self, cls: type, stash: Stash | None) -> None:
        self.cls = cls
        self.config = cls.__snapclass_config__
        self.stash = stash or self.config.stash
        self.fields = _pattern_fields(self.config.pattern or "")
        self.root, self.relative_pattern = self._split_root()
        self.has_recursive_wildcard = any(
            part == "*" for part in Path(self.relative_pattern).parts
        )
        self.regex = self._compile_regex()

    def iter_candidates(self) -> Iterator[Path]:
        if not self.root.exists():
            return iter(())
        static_parts = []
        for part in Path(self.relative_pattern).parts:
            if "{self." in part or part == "*":
                break
            static_parts.append(part)
        search_root = self.root.joinpath(*static_parts) if static_parts else self.root
        if not search_root.exists():
            return iter(())
        matches = [path for path in search_root.rglob("*") if self.regex.match(_as_posix(path))]
        return iter(sorted(matches, key=_as_posix))

    def values_from(self, path: Path) -> list[str]:
        match = self.regex.match(_as_posix(path))
        if not match:
            return []
        return [match.group(field) for field in self.fields]

    def _split_root(self) -> tuple[Path, str]:
        pattern = self.config.pattern or ""
        if self.stash is not None:
            _reject_relative_traversal(Path(pattern), "snapshot pattern")
            return self.stash.path, pattern
        path = Path(pattern)
        if path.is_absolute():
            return _split_absolute_pattern(path)
        if _is_home_relative(path):
            return _split_absolute_pattern(path.expanduser())
        _reject_relative_traversal(path, "snapshot pattern")
        if pattern.startswith("./"):
            return Path.cwd(), pattern[2:]
        return self.config.module_dir or Path.cwd(), pattern

    def _compile_regex(self) -> re.Pattern[str]:
        sentinel = "__SNAPCLASS_RECURSIVE_WILDCARD__"
        absolute_pattern = _as_posix(self.root / Path(self.relative_pattern))
        absolute_pattern = absolute_pattern.replace("/*/", f"/{sentinel}/")
        pieces: list[str] = []
        index = 0
        used: set[str] = set()
        for match in re.finditer(r"\{self\.([A-Za-z_][A-Za-z0-9_]*)\}", absolute_pattern):
            pieces.append(re.escape(absolute_pattern[index : match.start()]))
            name = match.group(1)
            if name in used:
                pieces.append(fr"(?P={name})")
            else:
                pieces.append(fr"(?P<{name}>{self._placeholder_pattern(match)})")
                used.add(name)
            index = match.end()
        pieces.append(re.escape(absolute_pattern[index:]))
        body = "".join(pieces).replace(
            f"/{re.escape(sentinel)}/",
            r"/(?:[^/]+/)*",
        )
        return re.compile("^" + body + "$")

    def _placeholder_pattern(self, match: re.Match[str]) -> str:
        if self._can_span_directories(match):
            return r".+"
        return r"[^/\\]+"

    def _can_span_directories(self, match: re.Match[str]) -> bool:
        pattern = self.config.pattern or ""
        fields = _pattern_fields(pattern)
        name = match.group(1)
        if fields and name == fields[-1]:
            return False
        following = match.string[match.end() :]
        return following.startswith("/")


def _pattern_fields(pattern: str) -> list[str]:
    fields: list[str] = []
    for match in re.finditer(r"\{self\.([A-Za-z_][A-Za-z0-9_]*)\}", pattern):
        name = match.group(1)
        if name not in fields:
            fields.append(name)
    return fields


def _split_absolute_pattern(path: Path) -> tuple[Path, str]:
    if path.anchor:
        return Path(path.anchor), path.relative_to(path.anchor).as_posix()
    return path.parent, path.name


def _reject_relative_traversal(path: Path, label: str) -> None:
    if ".." in path.parts:
        raise ValueError(f"Relative {label} cannot traverse above its root: {path}")


class _FormatProxy:
    def __init__(self, instance: object) -> None:
        self._instance = instance

    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self._instance, "__dict__")
        if name in data:
            return safe_path_placeholder(name, data[name])
        return safe_path_placeholder(name, object.__getattribute__(self._instance, name))


def _as_posix(path: Path) -> str:
    return path.resolve().as_posix()


def _resolve_type_hints(cls: type) -> dict[str, Any]:
    frame = sys._getframe(2)
    try:
        hints = get_type_hints(cls, globalns=frame.f_globals, localns=frame.f_locals)
        _cache_dataclass_type_hints(cls, hints, frame.f_globals, frame.f_locals)
        return hints
    except Exception:
        return _safe_type_hints(cls)


def _safe_type_hints(cls: type) -> dict[str, Any]:
    try:
        hints = get_type_hints(cls)
        _cache_dataclass_type_hints(cls, hints, None, None)
        return hints
    except Exception:
        return {field.name: field.type for field in dataclasses.fields(cls)}


def _dataclass_type_hints(cls: type) -> dict[str, Any]:
    cached = getattr(cls, "__snapclass_type_hints__", None)
    if isinstance(cached, dict):
        return cached
    try:
        hints = get_type_hints(cls)
    except Exception:
        hints = {field.name: field.type for field in dataclasses.fields(cls)}
    _cache_dataclass_type_hints(cls, hints, None, None)
    return hints


def _cache_dataclass_type_hints(
    cls: type,
    hints: dict[str, Any],
    globalns: dict[str, Any] | None,
    localns: dict[str, Any] | None,
    seen: set[type] | None = None,
) -> None:
    if not dataclasses.is_dataclass(cls):
        return
    if seen is None:
        seen = set()
    if cls in seen:
        return
    seen.add(cls)
    try:
        setattr(cls, "__snapclass_type_hints__", hints)
    except Exception:
        pass
    for hint in hints.values():
        for nested in _dataclass_hints_in(hint):
            try:
                nested_hints = get_type_hints(nested, globalns=globalns, localns=localns)
            except Exception:
                nested_hints = {field.name: field.type for field in dataclasses.fields(nested)}
            _cache_dataclass_type_hints(nested, nested_hints, globalns, localns, seen)


def _dataclass_hints_in(hint: Any) -> list[type]:
    if dataclasses.is_dataclass(hint):
        return [hint]
    nested: list[type] = []
    for arg in get_args(hint):
        nested.extend(_dataclass_hints_in(arg))
    return nested


def _module_dir_for(cls: type) -> Path | None:
    module = sys.modules.get(cls.__module__)
    filename = getattr(module, "__file__", None)
    if not filename:
        return None
    return Path(filename).resolve().parent


def _write_text_atomic(path: Path, text: str, *, write_delay: float | None = None) -> None:
    with _write_lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=os.fspath(path.parent), text=True
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            _replace_path_atomic(temp_path, path)
            if write_delay is None:
                write_delay = sessions.WRITE_DELAY
            if write_delay:
                time.sleep(write_delay)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            finally:
                raise


def _write_lock_for(path: Path) -> threading.RLock:
    try:
        key = path.resolve()
    except FileNotFoundError:
        key = path.absolute()
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _WRITE_LOCKS[key] = lock
        return lock


def _replace_path_atomic(temp_path: Path, path: Path) -> None:
    attempts = 5 if os.name == "nt" else 1
    for attempt in range(attempts):
        try:
            temp_path.replace(path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.01 * (attempt + 1))


def _auto_enabled(config: Config, instance: object | None = None) -> bool:
    if not sessions.HOOKS_ENABLED or config.manual:
        return False
    if instance is not None and getattr(instance, "_snapclass_hooks_suppressed", False):
        return False
    return True


def _lookup(item: object, key: str) -> Any:
    value = item
    for part in key.split("__"):
        if isinstance(value, dict):
            value = value[part]
        else:
            value = getattr(value, part)
    return value
