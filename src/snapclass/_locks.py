from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class _PathLockState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.depth = 0
        self.handle: BinaryIO | None = None


_LOCKS: dict[Path, _PathLockState] = {}
_LOCKS_GUARD = threading.Lock()


def write_lock_for(path: Path) -> threading.RLock:
    return _lock_state_for(path).lock


@contextmanager
def locked_path(path: Path) -> Iterator[None]:
    normalized_path = _normalized_path(path)
    state = _lock_state_for(normalized_path)
    state.lock.acquire()
    try:
        if state.depth == 0:
            state.handle = _acquire_os_lock(normalized_path)
        state.depth += 1
        try:
            yield
        finally:
            state.depth -= 1
            if state.depth == 0:
                handle = state.handle
                state.handle = None
                if handle is not None:
                    _release_os_lock(handle)
    finally:
        state.lock.release()


def _lock_state_for(path: Path) -> _PathLockState:
    key = _normalized_path(path)
    with _LOCKS_GUARD:
        state = _LOCKS.get(key)
        if state is None:
            state = _PathLockState()
            _LOCKS[key] = state
        return state


def _normalized_path(path: Path) -> Path:
    return path.resolve(strict=False)


def _lock_path_for(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


def _acquire_os_lock(path: Path) -> BinaryIO:
    lock_path = _lock_path_for(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        if os.name == "nt":
            _acquire_windows_lock(handle)
        else:
            _acquire_posix_lock(handle)
    except Exception:
        handle.close()
        raise
    return handle


def _release_os_lock(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":
            _release_windows_lock(handle)
        else:
            _release_posix_lock(handle)
    finally:
        handle.close()


def _acquire_windows_lock(handle: BinaryIO) -> None:
    import msvcrt

    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    while True:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            time.sleep(0.05)


def _release_windows_lock(handle: BinaryIO) -> None:
    import msvcrt

    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _acquire_posix_lock(handle: BinaryIO) -> None:
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _release_posix_lock(handle: BinaryIO) -> None:
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
