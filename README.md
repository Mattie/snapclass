# snapclass

Human-readable file persistence for Python dataclasses, an adaptation of the cool [`datafiles` project](https://github.com/jacebrowning/datafiles).

`snapclass` is a small persistence layer built around Python dataclasses.
Decorate a dataclass, give it a path pattern, and its instances can save and
load themselves as readable YAML, JSON, TOML, or text.

It is built for the stuff that belongs in a repo or project folder: prompts,
configs, fixtures, lightweight app state, and little durable objects with names.

You can use a `Stash` to pick a different location for the
serialized file (with env overrides) and have special format rules when you
need. You can also include a `sidecar` when you have a doc or binary you want to save next to it.



## ORM is a snap!

```python
from snapclass import snapclass

@snapclass("{self.slug}.yml")
class Note:
    slug: str
    title: str
    body: str = ""

Note(
    "first_note",
    "Today I used snapclass!",
    "My first day using snapclass. It saved my notes to YAML for me.",
).snapshot.save()
same_note = Note.snapshots.get("first_note")
```

```python
from snapclass import snapclass, Stash, Fresh

# Create a default location with an environment override.
runsloc = Stash("./runs", env="RUNS_DIR")

@snapclass("{self.name}.yml", stash=runsloc)
class RunData:
    name: str
    # shortcuts for common boilerplate factory code
    metrics: dict[str, float] = Fresh.Dict

RunData("baseline", {"accuracy": 0.98, "loss": 0.04}).snapshot.save()
```

```python
from snapclass import snapclass, Stash, sidecar

@snapclass
class Style:
    voice: str
    temperature: float

# Locations can be nested with stashes.
app = Stash("./myapp", env="MYAPP_DATA")
articles = app / "articles"

@snapclass("{self.slug}/article.yml", stash=articles)
class Article:
    slug: str
    title: str
    style: Style
    body: str = sidecar.text("{self.slug}.md")

article = Article(
    "dusk-court",
    "Dusk Court",
    Style("warm", 0.4),
    body="# Dusk Court\n\nBe brief, warm, and useful.\n",
)
loaded = Article.snapshots.get("dusk-court")
```

## FAQ

### Why use `snapclass` over `datafiles`?

`datafiles` is great and you should absolutely use it for your app or script! I love it so much and use it in project after project. After years of use, I've run into a few limitations-- like issues when multiple modules needed different `datafiles` behavior in one process (because much of the behavior control is global). I designed `snapclass` to isolate some of that via stashes and added some extra features I liked along the way.

## License

snapclass is licensed under the MIT License. See [LICENSE](LICENSE).

Much of snapclass's core behavior, along with portions of its implementation
and test suite, is adapted from the wonderful [`datafiles`](https://github.com/jacebrowning/datafiles) project by [Jace Browning](https://github.com/jacebrowning). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
