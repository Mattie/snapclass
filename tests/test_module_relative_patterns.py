from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def test_no_stash_relative_pattern_resolves_beside_defining_module(tmp_path):
    module_path = tmp_path / "fixture_models.py"
    module_path.write_text(
        "from snapclass import snapclass\n\n"
        "@snapclass('fixtures/{self.name}.yml', manual=True)\n"
        "class Prompt:\n"
        "    name: str\n"
        "    text: str = ''\n",
        encoding="utf-8",
    )

    module = _load_module("fixture_models", module_path)
    prompt = module.Prompt("Popsicle", "hello")
    prompt.snapshot.save()

    path = tmp_path / "fixtures" / "Popsicle.yml"
    assert prompt.snapshot.path == path.resolve()
    assert "text: hello" in path.read_text(encoding="utf-8")
    assert module.Prompt.snapshots.get("Popsicle").text == "hello"
    assert [item.name for item in module.Prompt.snapshots.all()] == ["Popsicle"]


def test_explicit_cwd_relative_pattern_keeps_cwd_semantics(tmp_path, monkeypatch):
    module_path = tmp_path / "cwd_models.py"
    module_path.write_text(
        "from snapclass import snapclass\n\n"
        "@snapclass('./runtime/{self.name}.yml', manual=True)\n"
        "class Run:\n"
        "    name: str\n"
        "    status: str = ''\n",
        encoding="utf-8",
    )
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    module = _load_module("cwd_models", module_path)
    run = module.Run("Scratch", "done")
    run.snapshot.save()

    path = cwd / "runtime" / "Scratch.yml"
    assert run.snapshot.path == path.resolve()
    assert "status: done" in path.read_text(encoding="utf-8")


def test_home_relative_pattern_works_with_collection_scans(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    from snapclass import snapclass

    @snapclass("~/{self.name}.yml", manual=True)
    class Prompt:
        name: str
        text: str = ""

    (home / "alpha.yml").write_text("text: loaded\n", encoding="utf-8")

    assert Prompt.snapshots.get("alpha").text == "loaded"
    assert [(item.name, item.text) for item in Prompt.snapshots.all()] == [
        ("alpha", "loaded")
    ]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    return module
