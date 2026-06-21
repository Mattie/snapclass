from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
import threading

import pytest

from snapclass import SnapclassError, serializers, sync
from snapclass.formatters import FileFormatter, YAMLFormatter


def test_sync_maps_existing_dataclass_instance_to_snapshot_file(tmp_path):
    @dataclass
    class Step:
        name: str
        status: str = "pending"

    @dataclass
    class Workflow:
        id: str
        steps: list[Step] = field(default_factory=list)

    workflow = Workflow("wf-1")
    sync(workflow, str(tmp_path / "{self.id}.yml"), manual=True)
    workflow.steps.append(Step("draft"))
    workflow.snapshot.save()

    text = (tmp_path / "wf-1.yml").read_text(encoding="utf-8")
    assert "draft" in text
    assert "pending" in text


def test_sync_workflow_lifecycle_snapshot_updates(tmp_path):
    @dataclass
    class Step:
        name: str
        status: str = "pending"
        error: str | None = None

    @dataclass
    class Workflow:
        id: str
        status: str = "created"
        steps: list[Step] = field(default_factory=list)

    workflow = Workflow("wf-life")
    sync(workflow, str(tmp_path / "{self.id}.yml"), manual=True, defaults=True)

    workflow.steps.append(Step("draft"))
    workflow.status = "running"
    workflow.snapshot.save()

    workflow.steps[0].status = "done"
    workflow.steps.append(Step("publish", "failed", "timeout"))
    workflow.status = "failed"
    workflow.snapshot.save()

    workflow.steps[1].status = "done"
    workflow.steps[1].error = None
    workflow.status = "complete"
    workflow.snapshot.save()

    data = YAMLFormatter.loads((tmp_path / "wf-life.yml").read_text(encoding="utf-8"))

    assert data == {
        "status": "complete",
        "steps": [
            {"name": "draft", "status": "done", "error": None},
            {"name": "publish", "status": "done", "error": None},
        ],
    }


def test_sync_uses_per_instance_pattern_for_same_dataclass(tmp_path):
    @dataclass
    class Workflow:
        id: str
        steps: list[str] = field(default_factory=list)

    first = Workflow("one", ["draft"])
    second = Workflow("two", ["publish"])

    sync(first, str(tmp_path / "first" / "{self.id}.yml"), manual=True)
    sync(second, str(tmp_path / "second" / "{self.id}.yml"), manual=True)

    first.snapshot.save()
    second.snapshot.save()

    first_path = tmp_path / "first" / "one.yml"
    second_path = tmp_path / "second" / "two.yml"

    assert first_path.exists()
    assert second_path.exists()
    assert "draft" in first_path.read_text(encoding="utf-8")
    assert "publish" in second_path.read_text(encoding="utf-8")
    assert not (tmp_path / "first" / "two.yml").exists()
    assert not (tmp_path / "second" / "one.yml").exists()


def test_sync_uses_per_instance_formatter_on_load(tmp_path):
    class UpperFileFormatter(FileFormatter):
        extensions = {".upper"}

        @classmethod
        def loads(cls, text: str):
            return {"value": text.strip().lower()}

        @classmethod
        def dumps(cls, data):
            return data["value"].upper() + "\n"

    @dataclass
    class Snapshot:
        id: str
        value: str = ""

    first = Snapshot("one", "alpha")
    second = Snapshot("two", "beta")

    sync(first, str(tmp_path / "{self.id}.yml"), manual=True)
    sync(second, str(tmp_path / "{self.id}.upper"), manual=True, formatter=UpperFileFormatter)

    first.snapshot.save()
    second.snapshot.save()

    assert (tmp_path / "one.yml").read_text(encoding="utf-8") == "value: alpha\n"
    assert (tmp_path / "two.upper").read_text(encoding="utf-8") == "BETA\n"

    (tmp_path / "two.upper").write_text("GAMMA\n", encoding="utf-8")
    second.snapshot.load()
    assert second.value == "gamma"


def test_sync_accepts_fields_projection_for_existing_dataclass(tmp_path):
    @dataclass
    class Workflow:
        id: str
        status: str = "created"
        transient: str = "runtime-only"

    workflow = Workflow("wf-projection", "running", "cache")
    sync(
        workflow,
        str(tmp_path / "{self.id}.yml"),
        manual=True,
        fields={"status": serializers.String},
    )

    workflow.snapshot.save()
    workflow.status = "complete"
    workflow.transient = "changed"
    workflow.snapshot.load()

    text = (tmp_path / "wf-projection.yml").read_text(encoding="utf-8")
    assert text == "status: running\n"
    assert workflow.status == "running"
    assert workflow.transient == "changed"


def test_sync_accepts_migrate_hook_for_existing_dataclass(tmp_path):
    @dataclass
    class Workflow:
        id: str
        status: str = ""

    path = tmp_path / "wf-legacy.yml"
    path.write_text("state: running\n", encoding="utf-8")

    workflow = Workflow("wf-legacy")
    sync(
        workflow,
        str(tmp_path / "{self.id}.yml"),
        manual=True,
        migrate=lambda data: {"status": data["state"]},
    )
    workflow.snapshot.load()

    assert workflow.status == "running"


def test_sync_accepts_conflict_policy_for_workflow_snapshots(tmp_path):
    @dataclass
    class Workflow:
        id: str
        status: str = "created"

    workflow = Workflow("wf-conflict", "running")
    sync(
        workflow,
        str(tmp_path / "{self.id}.yml"),
        manual=True,
        conflict="raise",
    )
    workflow.snapshot.save()
    (tmp_path / "wf-conflict.yml").write_text("status: human edit\n", encoding="utf-8")
    workflow.status = "complete"

    with pytest.raises(SnapclassError, match="externally modified"):
        workflow.snapshot.save()


def test_sync_snapshot_saves_are_serialized_across_threads(tmp_path):
    @dataclass
    class Step:
        name: str
        status: str = "pending"

    @dataclass
    class Workflow:
        id: str
        steps: list[Step] = field(default_factory=list)

    workflow = Workflow("wf-threaded")
    sync(workflow, str(tmp_path / "{self.id}.yml"), manual=True)

    start = threading.Barrier(6)
    mutation_lock = threading.Lock()

    def worker(index: int) -> None:
        start.wait()
        for revision in range(10):
            with mutation_lock:
                workflow.steps.append(Step(f"step-{index}-{revision}", "done"))
                workflow.snapshot.save()

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(worker, index) for index in range(5)]
        start.wait()
        for future in futures:
            future.result()

    path = tmp_path / "wf-threaded.yml"
    data = YAMLFormatter.loads(path.read_text(encoding="utf-8"))

    assert len(data["steps"]) == 50
    assert {step["name"] for step in data["steps"]} == {
        f"step-{index}-{revision}" for index in range(5) for revision in range(10)
    }


def test_concurrent_snapshot_saves_leave_complete_parseable_file(tmp_path):
    @dataclass
    class Step:
        name: str
        status: str = "pending"

    @dataclass
    class Workflow:
        id: str
        steps: list[Step] = field(default_factory=list)

    workflow = Workflow(
        "wf-concurrent",
        [Step(f"step-{index}", "done") for index in range(25)],
    )
    sync(workflow, str(tmp_path / "{self.id}.yml"), manual=True)

    start = threading.Barrier(9)

    def worker() -> None:
        start.wait()
        workflow.snapshot.save()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker) for _ in range(8)]
        start.wait()
        for future in futures:
            future.result()

    path = tmp_path / "wf-concurrent.yml"
    data = YAMLFormatter.loads(path.read_text(encoding="utf-8"))

    assert len(data["steps"]) == 25
    assert {step["name"] for step in data["steps"]} == {
        f"step-{index}" for index in range(25)
    }
    assert not list(tmp_path.glob("*.tmp"))
