from __future__ import annotations

import importlib.util
from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("mypy") is None,
    reason="mypy is not installed",
)

ROOT = Path(__file__).resolve().parents[1]


def run_mypy(tmp_path: Path, source: str, *, plugin: str) -> tuple[str, str, int]:
    from mypy import api

    source_path = tmp_path / "sample.py"
    source_path.write_text(dedent(source).strip() + "\n", encoding="utf-8")
    cache_path = tmp_path / ".mypy_cache"
    config_path = tmp_path / "mypy.ini"
    config_path.write_text(
        dedent(
            f"""
            [mypy]
            plugins = {plugin}
            mypy_path = {ROOT / "src"}
            cache_dir = {cache_path}
            show_error_codes = True
            no_error_summary = True
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    return api.run([str(source_path), "--config-file", str(config_path)])


def test_snapclass_plugin_types_public_magic_surfaces(tmp_path):
    stdout, stderr, status = run_mypy(
        tmp_path,
        """
        from snapclass import Stash, snapclass

        @snapclass("{self.name}.yml", stash=Stash("prompts"), manual=True)
        class Chat:
            name: str
            text: str = ""

        chat = Chat("Popsicle", "hello")
        loaded = Chat.snapshots.get("Popsicle")
        alternate = Chat.snapshots(Stash("scratch")).get("Scratch")
        chat.snapshot.save()

        @snapclass
        class Params:
            model: str
            temperature: float = 0.0

        params = Params("gpt-5-chat-latest")
        """,
        plugin="snapclass.plugins:mypy",
    )

    assert status == 0, stdout + stderr


def test_snapclass_plugin_reveals_decorated_magic_attribute_types(tmp_path):
    stdout, stderr, status = run_mypy(
        tmp_path,
        """
        from snapclass import Stash, snapclass

        @snapclass("{self.name}.yml", stash=Stash("prompts"), manual=True)
        class Chat:
            name: str
            text: str = ""

        chat = Chat("Popsicle", "hello")
        reveal_type(Chat.snapshots)
        reveal_type(Chat.snapshots.get("Popsicle"))
        reveal_type(Chat.snapshots(Stash("scratch")))
        reveal_type(Chat.snapshots(Stash("scratch")).get("Scratch"))
        reveal_type(chat.snapshot)
        """,
        plugin="snapclass.plugins:mypy",
    )

    assert status == 0, stdout + stderr
    assert stderr == ""
    assert stdout.count('Revealed type is "Any"') == 5


def test_plugin_preserves_dataclass_constructor_and_attribute_errors(tmp_path):
    stdout, stderr, status = run_mypy(
        tmp_path,
        """
        from snapclass import Stash, snapclass

        @snapclass("{self.name}.yml", stash=Stash("prompts"), manual=True)
        class Chat:
            name: str
            count: int = 0

        Chat(123)
        Chat("Popsicle", count="many")
        chat = Chat("Popsicle")
        chat.count = "many"
        """,
        plugin="snapclass.plugins:mypy",
    )

    assert status != 0
    assert "arg-type" in stdout
    assert "assignment" in stdout
    assert stderr == ""


def test_snapclass_decorator_module_import_type_checks(tmp_path):
    stdout, stderr, status = run_mypy(
        tmp_path,
        """
        from snapclass import Stash
        from snapclass.decorators import snapclass

        @snapclass("{self.name}.yml", stash=Stash("prompts"), manual=True)
        class Prompt:
            name: str
            text: str = ""

        prompt = Prompt("Popsicle", "hello")
        Prompt.snapshots.get("Popsicle")
        prompt.snapshot.save()
        """,
        plugin="snapclass.plugins:mypy",
    )

    assert status == 0, stdout + stderr


def test_plugin_does_not_bless_undecorated_classes_or_global_type_errors(tmp_path):
    stdout, stderr, status = run_mypy(
        tmp_path,
        """
        class Plain:
            pass

        Plain.snapshots
        plain = Plain()
        plain.snapshot
        value: int = "text"
        """,
        plugin="snapclass.plugins:mypy",
    )

    assert status != 0
    assert "attr-defined" in stdout
    assert "assignment" in stdout
    assert stderr == ""


def test_plugin_does_not_bless_unrelated_decorators_with_matching_names(tmp_path):
    stdout, stderr, status = run_mypy(
        tmp_path,
        """
        from typing import TypeVar

        T = TypeVar("T")

        def snapclass(cls: T) -> T:
            return cls

        @snapclass
        class Plain:
            pass

        Plain("x")
        Plain.snapshots
        """,
        plugin="snapclass.plugins:mypy",
    )

    assert status != 0
    assert "call-arg" in stdout
    assert "attr-defined" in stdout
    assert stderr == ""
