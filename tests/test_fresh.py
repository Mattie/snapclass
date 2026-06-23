from __future__ import annotations

from collections import Counter, defaultdict, deque
from pathlib import Path

from snapclass import Fresh, Stash, snapclass


def test_fresh_common_containers_are_independent_and_persist(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Run:
        name: str
        tags: list[str] = Fresh.List
        metrics: dict[str, float] = Fresh.Dict
        seen_ids: set[str] = Fresh.Set

    baseline = Run("baseline")
    challenger = Run("challenger")
    baseline.tags.append("winner")
    baseline.metrics["accuracy"] = 0.98
    baseline.seen_ids.add("a")
    baseline.snapshot.save()

    assert challenger.tags == []
    assert challenger.metrics == {}
    assert challenger.seen_ids == set()

    loaded = Run.snapshots.get("baseline")
    assert loaded.tags == ["winner"]
    assert loaded.metrics == {"accuracy": 0.98}
    assert loaded.seen_ids == {"a"}


def test_fresh_factory_and_copy_make_independent_defaults():
    @snapclass
    class Options:
        output_dir: Path = Fresh(lambda: Path("runs"))
        settings: dict[str, object] = Fresh.copy(
            {"enabled": True, "limits": {"retries": 3}}
        )

    first = Options()
    second = Options()
    first.output_dir = first.output_dir / "first"
    first.settings["limits"]["retries"] = 5

    assert second.output_dir == Path("runs")
    assert second.settings == {"enabled": True, "limits": {"retries": 3}}


def test_fresh_specialized_containers_cover_common_factory_lambdas():
    @snapclass
    class Index:
        queue: deque[str] = Fresh.Deque
        counts: Counter[str] = Fresh.Counter
        groups: defaultdict[str, list[str]] = Fresh.DefaultDict(list)
        named_groups: defaultdict[str, list[str]] = Fresh.DefaultDict(value=list)

    first = Index()
    second = Index()
    first.queue.append("compile")
    first.counts.update(["todo", "todo"])
    first.groups["chapter"].append("intro")
    first.named_groups["appendix"].append("index")

    assert list(second.queue) == []
    assert second.counts == Counter()
    assert isinstance(first.groups, defaultdict)
    assert first.groups.default_factory is list
    assert first.named_groups == {"appendix": ["index"]}
    assert second.named_groups == {}
    assert second.groups == {}


def test_fresh_defaultdict_keeps_missing_value_factory_after_reload(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Index:
        name: str
        groups: defaultdict[str, list[str]] = Fresh.DefaultDict(list)

    index = Index("main")
    index.groups["chapter"].append("intro")
    index.snapshot.save()

    loaded = Index.snapshots.get("main")
    loaded.groups["appendix"].append("index")

    assert isinstance(loaded.groups, defaultdict)
    assert loaded.groups.default_factory is list
    assert loaded.groups == {"chapter": ["intro"], "appendix": ["index"]}


def test_fresh_counter_and_deque_round_trip_with_snapclass(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Index:
        name: str
        queue: deque[str] = Fresh.Deque
        counts: Counter[str] = Fresh.Counter

    index = Index("main")
    index.queue.append("compile")
    index.counts.update(["todo", "todo"])
    index.snapshot.save()

    loaded = Index.snapshots.get("main")
    loaded.queue.append("ship")
    loaded.counts.update(["done"])

    assert isinstance(loaded.queue, deque)
    assert list(loaded.queue) == ["compile", "ship"]
    assert isinstance(loaded.counts, Counter)
    assert loaded.counts == Counter({"todo": 2, "done": 1})
