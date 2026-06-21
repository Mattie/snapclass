from __future__ import annotations

from dataclasses import field

import pytest

from snapclass import SnapclassError, Stash, snapclass


def test_unknown_fields_are_ignored_by_default(tmp_path):
    @snapclass("{self.name}.yml", stash=Stash(tmp_path), manual=True)
    class Chat:
        name: str
        message: str

    (tmp_path / "Popsicle.yml").write_text(
        "message: hello\nprovider_response_id: resp-1\n",
        encoding="utf-8",
    )

    chat = Chat.snapshots.get("Popsicle")
    chat.message = "updated"
    chat.snapshot.save()

    text = (tmp_path / "Popsicle.yml").read_text(encoding="utf-8")
    assert "message: updated" in text
    assert "provider_response_id" not in text


def test_unknown_fields_can_be_preserved_for_human_edited_yaml(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        unknown="preserve",
    )
    class Chat:
        name: str
        message: str

    (tmp_path / "Popsicle.yml").write_text(
        "message: hello\n"
        "provider_response_id: resp-1\n"
        "runtime:\n"
        "  status: done\n",
        encoding="utf-8",
    )

    chat = Chat.snapshots.get("Popsicle")
    chat.message = "updated"
    chat.snapshot.save()

    text = (tmp_path / "Popsicle.yml").read_text(encoding="utf-8")
    assert "message: updated" in text
    assert "provider_response_id: resp-1" in text
    assert "runtime:\n  status: done" in text


def test_preserved_unknown_fields_are_cleared_when_removed_on_disk(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        unknown="preserve",
    )
    class Chat:
        name: str
        message: str

    path = tmp_path / "Popsicle.yml"
    path.write_text(
        "message: hello\nprovider_response_id: resp-1\n",
        encoding="utf-8",
    )

    chat = Chat.snapshots.get("Popsicle")
    path.write_text("message: edited\n", encoding="utf-8")

    chat.snapshot.load()
    chat.message = "saved"
    chat.snapshot.save()

    text = path.read_text(encoding="utf-8")
    assert "message: saved" in text
    assert "provider_response_id" not in text


def test_unknown_fields_can_be_rejected_with_diagnostic(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        unknown="reject",
    )
    class Chat:
        name: str
        message: str

    (tmp_path / "Popsicle.yml").write_text(
        "message: hello\nunexpected: value\n",
        encoding="utf-8",
    )

    with pytest.raises(SnapclassError, match="Unknown fields.*unexpected"):
        Chat.snapshots.get("Popsicle")


def test_migrate_hook_runs_before_unknown_policy_for_human_edited_yaml(tmp_path):
    def migrate(data, *, path):
        assert path == tmp_path / "Popsicle.yml"
        data["message"] = data.pop("body")

    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        unknown="reject",
        migrate=migrate,
    )
    class Chat:
        name: str
        message: str

    (tmp_path / "Popsicle.yml").write_text("body: hello\n", encoding="utf-8")

    assert Chat.snapshots.get("Popsicle").message == "hello"


def test_migrate_hook_rejects_non_mapping_return_with_diagnostic(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        migrate=lambda data: ["message", data["message"]],
    )
    class Chat:
        name: str
        message: str

    (tmp_path / "Popsicle.yml").write_text("message: hello\n", encoding="utf-8")

    with pytest.raises(SnapclassError, match="migrate returned list"):
        Chat.snapshots.get("Popsicle")


def test_migrate_hook_exception_includes_path_and_reason(tmp_path):
    def migrate(data, *, path):
        raise ValueError(f"cannot migrate {path.name}")

    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        migrate=migrate,
    )
    class Chat:
        name: str
        message: str

    path = tmp_path / "Popsicle.yml"
    path.write_text("message: hello\n", encoding="utf-8")

    with pytest.raises(SnapclassError) as error:
        Chat.snapshots.get("Popsicle")

    assert f"Failed to migrate {path}" in str(error.value)
    assert "cannot migrate Popsicle.yml" in str(error.value)


def test_migrate_hook_preserves_yaml_comments_when_returning_mapping(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        unknown="preserve",
        migrate=lambda data: data,
    )
    class Chat:
        name: str
        message: str

    path = tmp_path / "Popsicle.yml"
    path.write_text(
        "# author note\nmessage: hello\nprovider_response_id: resp-1\n",
        encoding="utf-8",
    )

    chat = Chat.snapshots.get("Popsicle")
    chat.message = "updated"
    chat.snapshot.save()

    text = path.read_text(encoding="utf-8")
    assert "# author note" in text
    assert "provider_response_id: resp-1" in text


def test_migrate_hook_named_path_parameter_can_still_be_data(tmp_path):
    def migrate(path):
        path["message"] = path.pop("body")

    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        migrate=migrate,
    )
    class Chat:
        name: str
        message: str

    (tmp_path / "Popsicle.yml").write_text("body: hello\n", encoding="utf-8")

    assert Chat.snapshots.get("Popsicle").message == "hello"


def test_migrate_hook_can_accept_positional_only_file_path(tmp_path):
    def migrate(data, path, /):
        assert path == tmp_path / "Popsicle.yml"
        return {"message": data["body"]}

    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        migrate=migrate,
    )
    class Chat:
        name: str
        message: str

    (tmp_path / "Popsicle.yml").write_text("body: hello\n", encoding="utf-8")

    assert Chat.snapshots.get("Popsicle").message == "hello"


def test_infer_loads_unknown_fields_as_dynamic_attributes(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        infer=True,
    )
    class Chat:
        name: str
        message: str

    path = tmp_path / "Popsicle.yml"
    path.write_text(
        "message: hello\n"
        "provider_response_id: resp-1\n"
        "runtime:\n"
        "  status: done\n",
        encoding="utf-8",
    )

    chat = Chat.snapshots.get("Popsicle")

    assert chat.snapshot.infer is True
    assert chat.provider_response_id == "resp-1"
    assert chat.runtime == {"status": "done"}

    chat.runtime["status"] = "saved"
    chat.new_field = "added"
    chat.snapshot.save()

    saved = path.read_text(encoding="utf-8")
    assert "provider_response_id: resp-1" in saved
    assert "status: saved" in saved
    assert "new_field: added" in saved


def test_infer_removes_stale_dynamic_attributes_on_reload(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        infer=True,
    )
    class Chat:
        name: str
        message: str

    path = tmp_path / "Popsicle.yml"
    path.write_text("message: hello\ntransient: old\n", encoding="utf-8")
    chat = Chat.snapshots.get("Popsicle")
    assert chat.transient == "old"

    path.write_text("message: updated\n", encoding="utf-8")
    chat.snapshot.load()
    chat.snapshot.save()

    assert not hasattr(chat, "transient")
    assert "transient:" not in path.read_text(encoding="utf-8")


def test_unknown_fields_can_be_collected_into_extras_mapping(tmp_path):
    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        extras_field="extras",
    )
    class Chat:
        name: str
        message: str
        extras: dict = field(default_factory=dict)

    (tmp_path / "Popsicle.yml").write_text(
        "message: hello\n"
        "response_id: resp-1\n"
        "state:\n"
        "  status: done\n"
        "extras:\n"
        "  existing: keep\n",
        encoding="utf-8",
    )

    chat = Chat.snapshots.get("Popsicle")
    assert chat.extras == {
        "existing": "keep",
        "response_id": "resp-1",
        "state": {"status": "done"},
    }

    chat.snapshot.save()
    text = (tmp_path / "Popsicle.yml").read_text(encoding="utf-8")
    assert "response_id: resp-1" in text
    assert "state:" in text
    assert text.index("extras:") < text.index("response_id: resp-1")
    assert text.count("response_id: resp-1") == 1


def test_migrate_hook_runs_before_collecting_unknown_fields(tmp_path):
    def migrate(data):
        data["message"] = data.pop("body")
        data["provider_response_id"] = data.pop("response_id")
        return data

    @snapclass(
        "{self.name}.yml",
        stash=Stash(tmp_path),
        manual=True,
        extras_field="extras",
        migrate=migrate,
    )
    class Chat:
        name: str
        message: str
        extras: dict = field(default_factory=dict)

    (tmp_path / "Popsicle.yml").write_text(
        "body: hello\n"
        "response_id: resp-1\n"
        "extras:\n"
        "  existing: keep\n",
        encoding="utf-8",
    )

    chat = Chat.snapshots.get("Popsicle")

    assert chat.message == "hello"
    assert chat.extras == {
        "existing": "keep",
        "provider_response_id": "resp-1",
    }


def test_collect_policy_requires_a_real_extras_field(tmp_path):
    with pytest.raises(ValueError, match="extras_field"):

        @snapclass(
            "{self.name}.yml",
            stash=Stash(tmp_path),
            manual=True,
            unknown="collect",
            extras_field="extras",
        )
        class Chat:
            name: str
            message: str
