from __future__ import annotations

from dataclasses import field
import errno
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from snapclass import SnapclassError, Stash, snapclass
from snapclass._locks import (
    _is_windows_lock_contention,
    _lock_path_for,
    _normalized_path,
    locked_path,
)
from snapclass.formatters import YAMLFormatter


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    src = Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = os.fspath(src) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")


def test_lock_normalizes_missing_paths_through_symlinked_parent(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    _symlink_or_skip(link, real, target_is_directory=True)

    normalized = _normalized_path(link / "missing.yml")

    assert normalized == (real / "missing.yml").resolve(strict=False)
    assert _lock_path_for(normalized) == real / "missing.yml.lock"


def test_locked_path_uses_normalized_parent_for_lock_sidecar(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    _symlink_or_skip(link, real, target_is_directory=True)

    with locked_path(link / "state.yml"):
        assert (real / "state.yml.lock").exists()

    assert (link / "state.yml.lock").resolve() == (real / "state.yml.lock").resolve()


def test_locked_path_keeps_leaf_symlink_aligned_with_atomic_replace(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    target = real / "state.yml"
    target.write_text("steps:\n  - target\n", encoding="utf-8")
    alias = tmp_path / "alias.yml"
    _symlink_or_skip(alias, target)

    normalized = _normalized_path(alias)

    assert normalized == tmp_path / "alias.yml"
    assert _lock_path_for(normalized) == tmp_path / "alias.yml.lock"
    with locked_path(alias):
        assert (tmp_path / "alias.yml.lock").exists()

    assert not (real / "state.yml.lock").exists()


def test_snapshot_save_keeps_leaf_symlink_lock_aligned_with_replaced_path(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    target = real / "state.yml"
    target.write_text("steps:\n  - target\n", encoding="utf-8")
    alias = tmp_path / "alias.yml"
    _symlink_or_skip(alias, target)

    @snapclass(
        "alias.yml",
        stash=Stash(tmp_path),
        manual=True,
        write_strategy="atomic",
    )
    class State:
        steps: list[str] = field(default_factory=list)

    state = State.snapshots.get()
    state.steps.append("alias")

    with state.snapshot.locked():
        state.snapshot.save()
        assert (tmp_path / "alias.yml.lock").exists()
        assert _lock_path_for(_normalized_path(alias)) == tmp_path / "alias.yml.lock"
        assert not (real / "state.yml.lock").exists()

    assert not alias.is_symlink()
    assert YAMLFormatter.loads(alias.read_text(encoding="utf-8")) == {
        "steps": ["target", "alias"],
    }
    assert YAMLFormatter.loads(target.read_text(encoding="utf-8")) == {
        "steps": ["target"],
    }


def test_snapshot_save_resolves_leaf_symlink_lock_for_in_place_writes(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    target = real / "state.yml"
    target.write_text("steps:\n  - target\n", encoding="utf-8")
    alias = tmp_path / "alias.yml"
    _symlink_or_skip(alias, target)

    @snapclass("alias.yml", stash=Stash(tmp_path), manual=True)
    class State:
        steps: list[str] = field(default_factory=list)

    state = State.snapshots.get()
    state.steps.append("alias")

    with state.snapshot.locked():
        state.snapshot.save()
        assert (real / "state.yml.lock").exists()
        assert not (tmp_path / "alias.yml.lock").exists()

    assert alias.is_symlink()
    assert YAMLFormatter.loads(target.read_text(encoding="utf-8")) == {
        "steps": ["target", "alias"],
    }


def test_windows_lock_retry_classifier_only_accepts_lock_contention():
    msvcrt_lock_contention = OSError(errno.EACCES, "permission denied")
    lock_violation = OSError(errno.EACCES, "locked")
    lock_violation.winerror = 33
    sharing_violation = OSError(errno.EACCES, "sharing")
    sharing_violation.winerror = 32
    access_denied = OSError(errno.EACCES, "access denied")
    access_denied.winerror = 5
    bad_file_descriptor = OSError(errno.EBADF, "bad file descriptor")

    assert _is_windows_lock_contention(msvcrt_lock_contention) is True
    assert _is_windows_lock_contention(lock_violation) is True
    assert _is_windows_lock_contention(sharing_violation) is True
    assert _is_windows_lock_contention(access_denied) is False
    assert _is_windows_lock_contention(bad_file_descriptor) is False


def test_snapshot_locked_reloads_before_mutation_and_saves_inside_block(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class WorkflowState:
        name: str
        steps: list[str] = field(default_factory=list)

    WorkflowState("run", ["existing"]).snapshot.save()
    stale = WorkflowState("run", ["stale-local"])

    with stale.snapshot.locked(reload=True):
        assert stale.steps == ["existing"]
        stale.steps.append("started")
        stale.snapshot.save()

    data = YAMLFormatter.loads((tmp_path / "run.yml").read_text(encoding="utf-8"))
    assert data["steps"] == ["existing", "started"]


def test_snapshot_locked_allows_first_save_when_file_is_missing(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class WorkflowState:
        name: str
        steps: list[str] = field(default_factory=list)

    state = WorkflowState("run")

    with state.snapshot.locked(reload=True):
        state.steps.append("created")
        state.snapshot.save()

    assert YAMLFormatter.loads((tmp_path / "run.yml").read_text(encoding="utf-8")) == {
        "steps": ["created"],
    }


def test_require_lock_blocks_save_outside_locked_context(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        require_lock=True,
    )
    class WorkflowState:
        name: str
        steps: list[str] = field(default_factory=list)

    state = WorkflowState("run")
    state.steps.append("started")

    with pytest.raises(SnapclassError, match="active snapshot lock"):
        state.snapshot.save()

    with state.snapshot.locked(reload=True):
        state.snapshot.save()

    assert YAMLFormatter.loads((tmp_path / "run.yml").read_text(encoding="utf-8")) == {
        "steps": ["started"],
    }


def test_require_lock_rejects_automatic_models(tmp_path):
    with pytest.raises(ValueError, match="manual=True"):

        @snapclass("{self.name}.yml", stash=Stash(tmp_path), require_lock=True)
        class WorkflowState:
            name: str


def test_require_lock_rejects_patternless_snapclass():
    with pytest.raises(ValueError, match="persisted snapshot pattern"):
        @snapclass(require_lock=True)
        class WorkflowState:
            name: str


def test_snapclass_rejects_lock_extension_snapshot_pattern(tmp_path):
    with pytest.raises(ValueError, match="reserved"):

        @snapclass("{self.name}.lock", stash=Stash(tmp_path), manual=True)
        class WorkflowState:
            name: str


def test_snapshot_path_rejects_dynamic_lock_extension_filename(tmp_path):
    @snapclass("{self.name}", stash=Stash(tmp_path), manual=True)
    class WorkflowState:
        name: str

    state = WorkflowState("run.lock")

    with pytest.raises(ValueError, match="reserved"):
        state.snapshot.save()

    with pytest.raises(ValueError, match="reserved"):
        state.snapshot.path = tmp_path / "manual.lock"


def test_snapshot_locked_rejects_path_changes_inside_lock(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class WorkflowState:
        name: str
        steps: list[str] = field(default_factory=list)

    state = WorkflowState("first")

    with pytest.raises(SnapclassError, match="path changed"):
        with state.snapshot.locked():
            state.name = "second"
            state.snapshot.save()


def test_cross_process_locked_reload_preserves_both_updates(tmp_path):
    script = r"""
from dataclasses import field
from pathlib import Path
import sys
import time

from snapclass import Stash, snapclass

root = Path(sys.argv[1])
label = sys.argv[2]
ready = Path(sys.argv[3])
start = Path(sys.argv[4])

@snapclass("{self.name}.yml", stash=Stash(root), manual=True, require_lock=True)
class WorkflowState:
    name: str
    steps: list[str] = field(default_factory=list)

ready.write_text("ready", encoding="utf-8")
while not start.exists():
    time.sleep(0.01)

state = WorkflowState("shared")
with state.snapshot.locked(reload=True):
    state.steps.append(label)
    time.sleep(0.1)
    state.snapshot.save()
"""

    env = _subprocess_env()
    start = tmp_path / "start"
    ready_files = [tmp_path / "first.ready", tmp_path / "second.ready"]
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                os.fspath(tmp_path),
                label,
                os.fspath(ready),
                os.fspath(start),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for label, ready in zip(("lur-id", "article-id"), ready_files)
    ]
    try:
        deadline = time.monotonic() + 10
        while not all(path.exists() for path in ready_files):
            assert time.monotonic() < deadline
            time.sleep(0.01)

        start.write_text("go", encoding="utf-8")

        results = [process.communicate(timeout=15) for process in processes]
        for process, (stdout, stderr) in zip(processes, results):
            assert process.returncode == 0, stdout + stderr
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()

    data = YAMLFormatter.loads((tmp_path / "shared.yml").read_text(encoding="utf-8"))
    assert len(data["steps"]) == 2
    assert set(data["steps"]) == {"lur-id", "article-id"}
    assert (tmp_path / "shared.yml.lock").exists()


def test_process_exit_releases_snapshot_lock(tmp_path):
    holder_script = r"""
from dataclasses import field
from pathlib import Path
import sys
import time

from snapclass import Stash, snapclass

root = Path(sys.argv[1])
ready = Path(sys.argv[2])

@snapclass("{self.name}.yml", stash=Stash(root), manual=True, require_lock=True)
class WorkflowState:
    name: str
    steps: list[str] = field(default_factory=list)

state = WorkflowState("shared")
with state.snapshot.locked(reload=True):
    ready.write_text("locked", encoding="utf-8")
    time.sleep(60)
"""
    writer_script = r"""
from dataclasses import field
from pathlib import Path
import sys

from snapclass import Stash, snapclass

root = Path(sys.argv[1])

@snapclass("{self.name}.yml", stash=Stash(root), manual=True, require_lock=True)
class WorkflowState:
    name: str
    steps: list[str] = field(default_factory=list)

state = WorkflowState("shared")
with state.snapshot.locked(reload=True):
    state.steps.append("after-kill")
    state.snapshot.save()
"""
    env = _subprocess_env()
    ready = tmp_path / "holder.ready"
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script, os.fspath(tmp_path), os.fspath(ready)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        holder.kill()
        holder.communicate(timeout=10)

        writer = subprocess.Popen(
            [sys.executable, "-c", writer_script, os.fspath(tmp_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        stdout, stderr = writer.communicate(timeout=10)
        assert writer.returncode == 0, stdout + stderr
    finally:
        if holder.poll() is None:
            holder.kill()

    data = YAMLFormatter.loads((tmp_path / "shared.yml").read_text(encoding="utf-8"))
    assert data["steps"] == ["after-kill"]


def test_get_or_create_uses_lock_for_require_lock_models(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        defaults=True,
        require_lock=True,
    )
    class WorkflowState:
        name: str
        steps: list[str] = field(default_factory=list)

    state = WorkflowState.snapshots.get_or_create("shared")

    assert state.steps == []
    assert YAMLFormatter.loads((tmp_path / "shared.yml").read_text(encoding="utf-8")) == {
        "steps": [None],
    }
