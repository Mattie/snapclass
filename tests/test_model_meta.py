from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from snapclass import SnapclassError, Model, Stash, serializers, create_model


def test_model_exposes_default_meta_configuration():
    assert Model.Meta.snapshot_fields is None
    assert Model.Meta.snapshot_pattern is None
    assert Model.Meta.snapshot_manual is False
    assert Model.Meta.snapshot_defaults is False
    assert Model.Meta.snapshot_infer is False
    assert Model.Meta.snapshot_stash is None
    assert Model.Meta.snapshot_unknown == "ignore"
    assert Model.Meta.snapshot_conflict == "overwrite"


def test_model_meta_declaration_uses_snapclass_configuration(tmp_path):
    root = Stash(tmp_path / "items")

    class Item(Model):
        name: str
        count: int = 0
        tags: list[str] = field(default_factory=list)

        class Meta:
            snapshot_pattern = "{self.name}.yml"
            snapshot_stash = root
            snapshot_manual = True
            snapshot_defaults = True

    item = Item("Alpha", count=2)

    assert item.snapshot.path == tmp_path / "items" / "Alpha.yml"
    assert not item.snapshot.exists

    item.tags.append("fixture")
    item.snapshot.save()

    saved = (tmp_path / "items" / "Alpha.yml").read_text(encoding="utf-8")
    assert "count: 2" in saved
    assert "tags:" in saved
    assert Item.snapshots.get("Alpha").tags == ["fixture"]


def test_dataclass_model_meta_keeps_outer_dataclass_decorator_compatible(tmp_path):
    root = Stash(tmp_path / "notes")

    @dataclass
    class Note(Model):
        name: str
        body: str = ""

        class Meta:
            snapshot_pattern = "{self.name}"
            snapshot_stash = root

    note = Note("Popsicle", "No extension still uses YAML")

    path = tmp_path / "notes" / "Popsicle"
    assert path.exists()
    assert "body: No extension still uses YAML" in path.read_text(encoding="utf-8")
    assert Note.snapshots.get("Popsicle").body == "No extension still uses YAML"


def test_model_meta_snapshot_fields_apply_field_serializer(tmp_path):
    class TitleCase:
        @classmethod
        def to_preserialization_data(cls, value):
            return str(value).title()

        @classmethod
        def to_python_value(cls, value, **_kwargs):
            return str(value)

    class Prompt(Model):
        name: str
        label: str = ""

        class Meta:
            snapshot_pattern = "{self.name}.yml"
            snapshot_stash = Stash(tmp_path / "prompts")
            snapshot_manual = True
            snapshot_fields = {"label": TitleCase}

    Prompt("a", "hello world").snapshot.save()

    assert (tmp_path / "prompts" / "a.yml").read_text(encoding="utf-8") == (
        "label: Hello World\n"
    )
    assert Prompt.snapshots.get("a").label == "Hello World"


def test_model_meta_can_migrate_loaded_yaml_before_field_coercion(tmp_path):
    def migrate(data):
        data["model"] = data.pop("engine")

    class Prompt(Model):
        name: str
        model: str

        class Meta:
            snapshot_pattern = "{self.name}.yml"
            snapshot_stash = Stash(tmp_path / "prompts")
            snapshot_manual = True
            snapshot_migrate = migrate

    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "legacy.yml").write_text(
        "engine: gpt-5-chat-latest\n",
        encoding="utf-8",
    )

    assert Prompt.snapshots.get("legacy").model == "gpt-5-chat-latest"


def test_model_meta_snapshot_conflict_refuses_stale_save(tmp_path):
    class Prompt(Model):
        name: str
        body: str = ""

        class Meta:
            snapshot_pattern = "{self.name}.yml"
            snapshot_stash = Stash(tmp_path / "prompts")
            snapshot_manual = True
            snapshot_conflict = "raise"

    prompt = Prompt("popsicle", "first")
    prompt.snapshot.save()
    (tmp_path / "prompts" / "popsicle.yml").write_text(
        "body: human edit\n",
        encoding="utf-8",
    )
    prompt.body = "local edit"

    with pytest.raises(SnapclassError, match="externally modified"):
        prompt.snapshot.save()


def test_patternless_model_infers_fields_and_exposes_projection_without_path():
    @dataclass
    class Sample(Model):
        key: int
        name: str
        sschemas: float = 0.25

    sample = Sample(2, "b")

    assert sample.snapshot.path is None
    assert sample.snapshot.fields == {
        "key": serializers.Integer,
        "name": serializers.String,
        "sschemas": serializers.Float,
    }
    assert sample.snapshot.data == {"key": 2, "name": "b"}
    assert sample.snapshot.text == "key: 2\nname: b\n"

    with pytest.raises(RuntimeError, match="pattern"):
        sample.snapshot.save()

    with pytest.raises(RuntimeError, match="pattern"):
        list(Sample.snapshots.all())


def test_patternless_model_meta_fields_project_explicit_fields_only():
    @dataclass
    class Sample(Model):
        key: int
        name: str
        sschemas: float = 0.125
        extra: bool = True

        class Meta:
            snapshot_fields = {"name": serializers.String}

    sample = Sample(3, "c")

    assert sample.snapshot.path is None
    assert sample.snapshot.fields == {"name": serializers.String}
    assert sample.snapshot.data == {"name": "c"}
    assert sample.snapshot.text == "name: c\n"


def test_create_model_can_patch_existing_dataclass_with_pattern(tmp_path):
    @dataclass
    class Prompt:
        name: str
        text: str = ""

    create_model(Prompt, pattern=str(tmp_path / "{self.name}.yml"), manual=True)

    Prompt("a", "hello").snapshot.save()

    assert (tmp_path / "a.yml").read_text(encoding="utf-8") == "text: hello\n"
    assert Prompt.snapshots.get("a").text == "hello"


def test_create_model_accepts_direct_stash_binding(tmp_path):
    @dataclass
    class Prompt:
        name: str
        text: str = ""

    create_model(
        Prompt,
        pattern="{self.name}.yml",
        stash=Stash(tmp_path / "prompts"),
        manual=True,
    )

    Prompt("popsicle", "hello").snapshot.save()

    assert (tmp_path / "prompts" / "popsicle.yml").read_text(encoding="utf-8") == (
        "text: hello\n"
    )
    assert Prompt.snapshots.get("popsicle").text == "hello"


def test_create_model_direct_stash_overrides_meta_stash(tmp_path):
    @dataclass
    class Prompt:
        name: str
        text: str = ""

        class Meta:
            snapshot_pattern = "{self.name}.yml"
            snapshot_stash = Stash(tmp_path / "meta")
            snapshot_manual = True

    create_model(Prompt, stash=tmp_path / "override")

    Prompt("popsicle", "hello").snapshot.save()

    assert not (tmp_path / "meta" / "popsicle.yml").exists()
    assert (tmp_path / "override" / "popsicle.yml").read_text(encoding="utf-8") == (
        "text: hello\n"
    )


def test_create_model_supports_patternless_fields_projection():
    class OnlyName:
        @classmethod
        def to_preserialization_data(cls, value):
            return str(value).upper()

        @classmethod
        def to_python_value(cls, value):
            return str(value)

    @dataclass
    class Sample:
        key: int
        name: str
        extra: bool = True

    create_model(Sample, fields={"name": OnlyName})
    sample = Sample(1, "alpha")

    assert sample.snapshot.manual is True
    assert sample.snapshot.fields == {"name": OnlyName}
    assert sample.snapshot.data == {"name": "ALPHA"}


def test_create_model_patches_frozen_dataclass_with_meta_and_collection():
    @dataclass(frozen=True)
    class FrozenPrompt:
        name: str

    create_model(FrozenPrompt)

    prompt = FrozenPrompt("popsicle")

    assert FrozenPrompt.Meta.snapshot_pattern is None
    assert FrozenPrompt.Meta.snapshot_manual is True
    assert hasattr(FrozenPrompt, "snapshots")
    assert prompt.snapshot.path is None


def test_create_model_meta_reflects_normalized_unknown_policy():
    @dataclass
    class Prompt:
        name: str
        extras: dict = field(default_factory=dict)

        class Meta:
            snapshot_extras_field = "extras"

    create_model(Prompt)

    assert Prompt.Meta.snapshot_unknown == "collect"
    assert Prompt.Meta.snapshot_extras_field == "extras"


def test_create_model_accepts_migrate_hook(tmp_path):
    @dataclass
    class Prompt:
        name: str
        model: str = ""

    create_model(
        Prompt,
        pattern=str(tmp_path / "{self.name}.yml"),
        manual=True,
        migrate=lambda data: {"model": data["engine"]},
    )
    (tmp_path / "legacy.yml").write_text("engine: gpt-5-chat-latest\n", encoding="utf-8")

    assert Prompt.snapshots.get("legacy").model == "gpt-5-chat-latest"


def test_create_model_accepts_conflict_policy(tmp_path):
    @dataclass
    class Prompt:
        name: str
        body: str = ""

    create_model(
        Prompt,
        pattern=str(tmp_path / "{self.name}.yml"),
        manual=True,
        conflict="raise",
    )
    prompt = Prompt("popsicle", "first")
    prompt.snapshot.save()
    (tmp_path / "popsicle.yml").write_text("body: human edit\n", encoding="utf-8")
    prompt.body = "local edit"

    with pytest.raises(SnapclassError, match="externally modified"):
        prompt.snapshot.save()


def test_create_model_rejects_non_dataclass():
    class Plain:
        name: str

    with pytest.raises(ValueError, match="dataclass"):
        create_model(Plain, pattern="{self.name}.yml")
