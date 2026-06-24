from __future__ import annotations

from typing import Any

from .schemas import Snapshot, _attach_snapshot, _mark_snapshot_ready


def create_snapshot(obj: Any, root: Snapshot | None = None) -> Snapshot:
    snapshot = getattr(obj, "snapshot", None)
    if snapshot is not None:
        return snapshot
    config = obj.__class__.__snapclass_config__
    _attach_snapshot(obj, config, root.stash if root is not None else None)
    _mark_snapshot_ready(obj)
    return obj.snapshot


__all__ = ["Snapshot", "create_snapshot"]
