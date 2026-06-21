from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
import threading

import pytest

from snapclass import SnapclassError, Stash, snapclass


def test_all_matches_placeholder_directory_patterns(tmp_path):
    articles = Stash(tmp_path / "world") / "article"

    @snapclass("{self.slug}/article.yml", stash=articles, manual=True)
    class Article:
        slug: str
        title: str

    Article("alpha", "Alpha").snapshot.save()
    Article("beta", "Beta").snapshot.save()

    assert [article.slug for article in Article.snapshots.all()] == ["alpha", "beta"]


def test_all_extracts_multiple_placeholders_and_coerces_types(tmp_path):
    timelines = Stash(tmp_path / "world") / "timeline"

    @snapclass("{self.name}_v{self.revision}.yml", stash=timelines, manual=True, defaults=True)
    class Timeline:
        name: str
        revision: int
        eras: list[dict] = field(default_factory=list)

    Timeline("main", 1, [{"name": "Founding"}]).snapshot.save()
    Timeline("main", 2, [{"name": "Expansion"}]).snapshot.save()

    loaded = list(Timeline.snapshots.all())
    assert [(item.name, item.revision) for item in loaded] == [("main", 1), ("main", 2)]
    assert all(isinstance(item.revision, int) for item in loaded)
    assert [item.revision for item in Timeline.snapshots.filter(revision=2)] == [2]


def test_collection_all_returns_matches_in_path_order(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Prompt:
        name: str
        text: str = ""

    Prompt("charlie", "third").snapshot.save()
    Prompt("alpha", "first").snapshot.save()
    Prompt("bravo", "second").snapshot.save()

    assert [prompt.name for prompt in Prompt.snapshots.all()] == [
        "alpha",
        "bravo",
        "charlie",
    ]


def test_collection_all_honors_repeated_placeholder_segments(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}/{self.name}.yml", stash=root, manual=True)
    class Prompt:
        name: str
        text: str = ""

    Prompt("alpha", "valid").snapshot.save()
    mismatched = tmp_path / "beta" / "alpha.yml"
    mismatched.parent.mkdir()
    mismatched.write_text("text: invalid\n", encoding="utf-8")

    loaded = list(Prompt.snapshots.all())

    assert [(prompt.name, prompt.text) for prompt in loaded] == [("alpha", "valid")]
    assert loaded[0].snapshot.path == tmp_path / "alpha" / "alpha.yml"


def test_collection_get_uses_dataclass_default_for_missing_placeholder(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}/{self.variant}.yml", stash=root, manual=True)
    class Template:
        name: str
        variant: str = "default"
        body: str = ""

    Template("welcome", "default", "Hello").snapshot.save()

    loaded = Template.snapshots.get("welcome")

    assert loaded.variant == "default"
    assert loaded.body == "Hello"
    assert loaded.snapshot.path == tmp_path / "welcome" / "default.yml"


def test_collection_get_or_create_uses_dataclass_default_for_missing_placeholder(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}/{self.variant}.yml", stash=root, manual=True, defaults=True)
    class Template:
        name: str
        variant: str = "default"
        body: str = "created"

    created = Template.snapshots.get_or_create("welcome")

    assert created.variant == "default"
    assert created.body == "created"
    assert created.snapshot.path == tmp_path / "welcome" / "default.yml"
    assert (tmp_path / "welcome" / "default.yml").exists()


def test_collection_all_with_defaulted_placeholder_loads_matching_files(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}/{self.variant}.yml", stash=root, manual=True)
    class Template:
        name: str
        variant: str = "default"
        body: str = ""

    Template("welcome", "default", "Hello").snapshot.save()
    Template("welcome", "short", "Hi").snapshot.save()

    loaded = sorted(Template.snapshots.all(), key=lambda item: item.variant)

    assert [(item.name, item.variant, item.body) for item in loaded] == [
        ("welcome", "default", "Hello"),
        ("welcome", "short", "Hi"),
    ]


def test_collection_all_loads_required_non_placeholder_fields(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Prompt:
        name: str
        text: str

    (tmp_path / "alpha.yml").write_text("text: loaded\n", encoding="utf-8")

    loaded = list(Prompt.snapshots.all())

    assert [(item.name, item.text) for item in loaded] == [("alpha", "loaded")]


def test_collection_all_fills_omitted_required_scalars_from_serializer_defaults(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.pk}.yml", stash=root, manual=True)
    class InventoryItem:
        pk: int
        name: str
        unit_price: float
        quantity_on_hand: int = 0

    (tmp_path / "42.yml").write_text("name: Things\n", encoding="utf-8")

    loaded = list(InventoryItem.snapshots.all())

    assert [(item.pk, item.name, item.unit_price, item.quantity_on_hand) for item in loaded] == [
        (42, "Things", 0.0, 0)
    ]


def test_partial_nested_dataclass_load_fills_omitted_scalar_fields(tmp_path):
    @dataclass
    class Nested:
        name: str
        sschemas: float
        weight: int | None

    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Sample:
        name: str
        nested: Nested

    (tmp_path / "sample.yml").write_text(
        "nested:\n  name: bar\n",
        encoding="utf-8",
    )

    loaded = Sample.snapshots.get("sample")

    assert loaded.nested == Nested("bar", 0.0, None)


def test_collection_filter_supports_nested_dataclass_and_dict_fields(tmp_path):
    root = Stash(tmp_path)

    @dataclass
    class Params:
        model: str

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Prompt:
        name: str
        params: Params
        metadata: dict

    Prompt("alpha", Params("gpt-5-chat-latest"), {"owner": {"name": "Mattie"}}).save()
    Prompt("beta", Params("other"), {"owner": {"name": "Mattie"}}).save()

    loaded = list(
        Prompt.snapshots.filter(params__model="gpt-5-chat-latest", metadata__owner__name="Mattie")
    )

    assert [(item.name, item.params.model) for item in loaded] == [
        ("alpha", "gpt-5-chat-latest")
    ]


def test_all_supports_recursive_star_path_segment(tmp_path):
    root = Stash(tmp_path)

    @snapclass("prompts/*/{self.name}.yml", stash=root, manual=True)
    class Prompt:
        name: str
        text: str = ""

    nested = tmp_path / "prompts" / "authors" / "mattie"
    shallow = tmp_path / "prompts"
    nested.mkdir(parents=True)
    (nested / "Popsicle.yml").write_text("text: nested\n", encoding="utf-8")
    (shallow / "Scratch.yml").write_text("text: shallow\n", encoding="utf-8")

    loaded = sorted(Prompt.snapshots.all(), key=lambda prompt: prompt.name)

    assert [(prompt.name, prompt.text) for prompt in loaded] == [
        ("Popsicle", "nested"),
        ("Scratch", "shallow"),
    ]
    assert loaded[0].snapshot.path == nested / "Popsicle.yml"
    assert loaded[1].snapshot.path == shallow / "Scratch.yml"


def test_all_can_extract_path_valued_placeholder_from_nested_files(tmp_path):
    root = Stash(tmp_path)

    @snapclass("routes/{self.route}/{self.variant}.yml", stash=root, manual=True)
    class RouteConfig:
        route: str
        variant: str
        value: int = 0

    (tmp_path / "routes" / "foo").mkdir(parents=True)
    (tmp_path / "routes" / "foo" / "public.yml").write_text("value: 1\n", encoding="utf-8")
    (tmp_path / "routes" / "foo" / "bar").mkdir(parents=True)
    nested_path = tmp_path / "routes" / "foo" / "bar" / "private.yml"
    nested_path.write_text("value: 2\n", encoding="utf-8")

    loaded = sorted(RouteConfig.snapshots.all(), key=lambda item: item.variant)

    assert [(item.route, item.variant, item.value) for item in loaded] == [
        ("foo/bar", "private", 2),
        ("foo", "public", 1),
    ]

    loaded[0].value = 3
    loaded[0].snapshot.save()

    assert nested_path.read_text(encoding="utf-8") == "value: 3\n"


def test_collection_exclude_skips_before_loading(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Prompt:
        name: str
        text: str

    Prompt("keep", "yes").snapshot.save()
    Prompt("skip-this", "no").snapshot.save()

    assert [item.name for item in Prompt.snapshots.all(_exclude="skip")] == ["keep"]


def test_collection_get_preserves_invalid_yaml_and_reports_path(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Prompt:
        name: str
        text: str = ""

    broken_text = "text: [unterminated\n"
    broken_path = tmp_path / "broken.yml"
    broken_path.write_text(broken_text, encoding="utf-8")

    with pytest.raises(SnapclassError) as exc_info:
        Prompt.snapshots.get("broken")

    message = str(exc_info.value)
    assert str(broken_path) in message
    assert "line 1" in message
    assert "column" in message
    assert broken_path.read_text(encoding="utf-8") == broken_text


def test_collection_all_preserves_invalid_yaml_and_reports_path(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}.yml", stash=root, manual=True)
    class Prompt:
        name: str
        text: str = ""

    Prompt("good", "ok").snapshot.save()
    broken_text = "text: [unterminated\n"
    broken_path = tmp_path / "broken.yml"
    broken_path.write_text(broken_text, encoding="utf-8")

    with pytest.raises(SnapclassError) as exc_info:
        list(Prompt.snapshots.all())

    message = str(exc_info.value)
    assert str(broken_path) in message
    assert "line 1" in message
    assert "column" in message
    assert broken_path.read_text(encoding="utf-8") == broken_text


def test_concurrent_get_or_create_for_same_key_returns_equivalent_snapshots(tmp_path):
    root = Stash(tmp_path)

    @snapclass("{self.name}.yml", stash=root, manual=True, defaults=True)
    class Prompt:
        name: str
        text: str = "created"

    start = threading.Barrier(9)

    def worker():
        start.wait()
        item = Prompt.snapshots.get_or_create("shared")
        return item.name, item.text, item.snapshot.path

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker) for _ in range(8)]
        start.wait()
        results = [future.result() for future in futures]

    assert results == [("shared", "created", tmp_path / "shared.yml")] * 8
    assert list(tmp_path.glob("shared.yml")) == [tmp_path / "shared.yml"]
    assert not list(tmp_path.glob("*.tmp"))
    assert "text: created" in (tmp_path / "shared.yml").read_text(encoding="utf-8")
