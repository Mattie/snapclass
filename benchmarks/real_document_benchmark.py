from __future__ import annotations

import argparse
import copy
import dataclasses
import keyword
import os
import platform
import statistics
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.is_dir():
    sys.path.insert(0, os.fspath(SRC))

from snapclass import Stash, snapclass, sync  # noqa: E402
from snapclass import _yaml_fast  # noqa: E402
from snapclass.formatters import (  # noqa: E402
    JSONFormatter,
    TERSEFormatter,
    YAMLFormatter,
)


FORMATS = {
    "yaml": (".yml", YAMLFormatter),
    "terse": (".terse", TERSEFormatter),
    "json": (".json", JSONFormatter),
}


@snapclass
class BenchmarkConfig:
    sources: list[str]
    iterations: int = 5
    warmups: int = 1
    large_threshold: int = 1_000_000
    large_iterations: int = 1
    large_warmups: int = 0
    encoding: str = "cl100k_base"
    output: str | None = None


@snapclass
class OperationMetrics:
    median_ms: float
    min_ms: float
    max_ms: float


@snapclass
class FormatMetrics:
    format: str
    extension: str
    bytes: int
    bytes_vs_json: float
    tokens: int
    tokens_vs_json: float
    accelerated_load: bool
    accelerated_scalar_save: bool
    accelerated_block_save: bool
    snapshot_data: OperationMetrics
    formatter_dump: OperationMetrics
    formatter_load: OperationMetrics
    snapshot_text: OperationMetrics
    snapshot_save_unchanged: OperationMetrics
    snapshot_save_scalar: OperationMetrics
    snapshot_save_block_scalar: OperationMetrics | None
    snapshot_save_structural: OperationMetrics | None
    collection_get: OperationMetrics
    snapshot_load: OperationMetrics


@snapclass
class DocumentMetrics:
    name: str
    source: str
    source_bytes: int
    source_accelerated: bool
    indexed_scalars: int
    indexed_block_scalars: int
    scalar_path: str
    block_scalar_path: str | None
    structural_path: str | None
    iterations: int
    warmups: int
    formats: list[FormatMetrics]


@snapclass
class YamlAcceleration:
    available: bool
    dependencies: dict[str, str]


@snapclass
class BenchmarkReport:
    timestamp: str
    python: str
    platform: str
    encoding: str
    yaml_acceleration: YamlAcceleration
    documents: list[DocumentMetrics]


def parse_args() -> BenchmarkConfig:
    parser = argparse.ArgumentParser(
        description="Benchmark real YAML documents through snapclass YAML, TERSE, and JSON.",
    )
    parser.add_argument("sources", nargs="+", help="YAML snapshot paths to benchmark")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--large-threshold", type=int, default=1_000_000)
    parser.add_argument("--large-iterations", type=int, default=1)
    parser.add_argument("--large-warmups", type=int, default=0)
    parser.add_argument("--encoding", default="cl100k_base")
    parser.add_argument("--output")
    args = parser.parse_args()
    config = BenchmarkConfig(
        sources=args.sources,
        iterations=args.iterations,
        warmups=args.warmups,
        large_threshold=args.large_threshold,
        large_iterations=args.large_iterations,
        large_warmups=args.large_warmups,
        encoding=args.encoding,
        output=args.output,
    )
    if config.iterations <= 0 or config.large_iterations <= 0:
        raise SystemExit("iteration counts must be greater than zero")
    if config.warmups < 0 or config.large_warmups < 0:
        raise SystemExit("warmup counts must be zero or greater")
    if config.large_threshold <= 0:
        raise SystemExit("--large-threshold must be greater than zero")
    if config.output is not None and Path(config.output).suffix.lower() not in {
        ".yml",
        ".yaml",
    }:
        raise SystemExit("--output must be a .yml or .yaml path")
    for source in config.sources:
        path = Path(source)
        if not path.is_file():
            raise SystemExit(f"source does not exist: {path}")
        if path.suffix.lower() not in {".yml", ".yaml"}:
            raise SystemExit(f"source must be YAML: {path}")
    return config


def load_tokenizer(encoding_name: str) -> Any:
    try:
        import tiktoken
    except ImportError as exc:
        raise SystemExit(
            "tiktoken is required for exact token counts. Install it with "
            "python.exe -m pip install -e .[benchmark]"
        ) from exc
    try:
        return tiktoken.get_encoding(encoding_name)
    except Exception as exc:
        raise SystemExit(f"Unable to load tiktoken encoding {encoding_name!r}: {exc}") from exc


def measure(operation: Any, *, warmups: int, iterations: int) -> OperationMetrics:
    for _ in range(warmups):
        operation()
    values: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        operation()
        values.append((time.perf_counter() - started) * 1000)
    return OperationMetrics(
        median_ms=round(statistics.median(values), 4),
        min_ms=round(min(values), 4),
        max_ms=round(max(values), 4),
    )


def benchmark_document(
    source: Path,
    *,
    tokenizer: Any,
    config: BenchmarkConfig,
) -> DocumentMetrics:
    source_text = source.read_bytes().decode("utf-8")
    source_data = YAMLFormatter.loads(source_text)
    if not isinstance(source_data, dict):
        raise ValueError(f"source YAML root must be a mapping: {source}")
    fast_loaded = _yaml_fast.load(source_text)
    fast_state = fast_loaded[1] if fast_loaded is not None else None
    scalar_path = _choose_scalar_path(source_data, fast_state)
    block_scalar_path = _choose_block_scalar_path(source_data, fast_state)
    structural_path = _choose_list_path(source_data)
    source_bytes = len(source_text.encode("utf-8"))
    if source_bytes >= config.large_threshold:
        iterations = config.large_iterations
        warmups = config.large_warmups
    else:
        iterations = config.iterations
        warmups = config.warmups

    with tempfile.TemporaryDirectory(prefix="snapclass-real-document-") as root:
        metrics = [
            benchmark_format(
                format_name,
                source_text=source_text,
                source_data=source_data,
                scalar_path=scalar_path,
                block_scalar_path=block_scalar_path,
                structural_path=structural_path,
                tokenizer=tokenizer,
                root=Path(root),
                iterations=iterations,
                warmups=warmups,
            )
            for format_name in FORMATS
        ]
    _apply_ratios(metrics)
    return DocumentMetrics(
        name=source.name,
        source=os.fspath(source.resolve()),
        source_bytes=source_bytes,
        source_accelerated=fast_state is not None,
        indexed_scalars=len(fast_state.scalars) if fast_state is not None else 0,
        indexed_block_scalars=(
            len(fast_state.block_scalars) if fast_state is not None else 0
        ),
        scalar_path=_format_path(scalar_path),
        block_scalar_path=(
            _format_path(block_scalar_path) if block_scalar_path is not None else None
        ),
        structural_path=(
            _format_path(structural_path) if structural_path is not None else None
        ),
        iterations=iterations,
        warmups=warmups,
        formats=metrics,
    )


def benchmark_format(
    format_name: str,
    *,
    source_text: str,
    source_data: dict[str, Any],
    scalar_path: tuple[str | int, ...],
    block_scalar_path: tuple[str | int, ...] | None,
    structural_path: tuple[str | int, ...] | None,
    tokenizer: Any,
    root: Path,
    iterations: int,
    warmups: int,
) -> FormatMetrics:
    extension, formatter = FORMATS[format_name]
    stash = Stash(root / format_name)
    document_class = _document_class(format_name, source_data, stash)
    text = source_text if format_name == "yaml" else formatter.dumps(source_data)
    loaded_data = formatter.loads(text)
    if not _yaml_fast.semantic_equal(loaded_data, source_data):
        raise ValueError(f"{format_name} did not round-trip the source data")
    path = stash.path / f"document{extension}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    document = document_class("document", **copy.deepcopy(dict(source_data)))
    document.snapshot.load()
    accelerated_load = format_name == "yaml" and document.snapshot._yaml_state is not None

    data_metrics = measure(
        lambda: document.snapshot.data,
        warmups=warmups,
        iterations=iterations,
    )
    formatter_data = document.snapshot.data
    formatter_dump_metrics = measure(
        lambda: formatter.dumps(formatter_data),
        warmups=warmups,
        iterations=iterations,
    )
    formatter_load_metrics = measure(
        lambda: formatter.loads(text),
        warmups=warmups,
        iterations=iterations,
    )
    text_metrics = measure(
        lambda: document.snapshot.text,
        warmups=warmups,
        iterations=iterations,
    )
    collection = document_class.snapshots(stash)
    get_metrics = measure(
        lambda: collection.get("document"),
        warmups=warmups,
        iterations=iterations,
    )
    load_metrics = measure(
        document.snapshot.load,
        warmups=warmups,
        iterations=iterations,
    )
    unchanged_metrics = measure(
        document.snapshot.save,
        warmups=warmups,
        iterations=iterations,
    )

    original_value = _get_object_value(document, scalar_path)
    scalar_values = _scalar_variants(original_value)
    scalar_toggle = False

    def save_scalar() -> None:
        nonlocal scalar_toggle
        scalar_toggle = not scalar_toggle
        _set_object_value(
            document,
            scalar_path,
            scalar_values[0] if scalar_toggle else scalar_values[1],
        )
        document.snapshot.save()

    scalar_metrics = measure(
        save_scalar,
        warmups=warmups,
        iterations=iterations,
    )
    accelerated_scalar = (
        format_name == "yaml" and document.snapshot._yaml_state is not None
    )

    block_metrics = None
    accelerated_block = False
    if block_scalar_path is not None:
        original_block = _get_object_value(document, block_scalar_path)
        block_values = _block_scalar_variants(original_block)
        block_toggle = False

        def save_block_scalar() -> None:
            nonlocal block_toggle
            block_toggle = not block_toggle
            _set_object_value(
                document,
                block_scalar_path,
                block_values[0] if block_toggle else block_values[1],
            )
            document.snapshot.save()

        block_metrics = measure(
            save_block_scalar,
            warmups=warmups,
            iterations=iterations,
        )
        accelerated_block = (
            format_name == "yaml" and document.snapshot._yaml_state is not None
        )

    structural_metrics = None
    if structural_path is not None:
        structural_toggle = False

        def save_structural() -> None:
            nonlocal structural_toggle
            values = _get_object_value(document, structural_path)
            if structural_toggle:
                values.pop()
            else:
                values.append(None)
            structural_toggle = not structural_toggle
            document.snapshot.save()

        structural_metrics = measure(
            save_structural,
            warmups=warmups,
            iterations=iterations,
        )

    return FormatMetrics(
        format=format_name,
        extension=extension,
        bytes=len(text.encode("utf-8")),
        bytes_vs_json=1.0,
        tokens=len(tokenizer.encode(text)),
        tokens_vs_json=1.0,
        accelerated_load=accelerated_load,
        accelerated_scalar_save=accelerated_scalar,
        accelerated_block_save=accelerated_block,
        snapshot_data=data_metrics,
        formatter_dump=formatter_dump_metrics,
        formatter_load=formatter_load_metrics,
        snapshot_text=text_metrics,
        snapshot_save_unchanged=unchanged_metrics,
        snapshot_save_scalar=scalar_metrics,
        snapshot_save_block_scalar=block_metrics,
        snapshot_save_structural=structural_metrics,
        collection_get=get_metrics,
        snapshot_load=load_metrics,
    )


def _document_class(format_name: str, data: dict[str, Any], stash: Stash) -> type:
    fields: list[tuple[str, Any]] = [("benchmark_name", str)]
    for key in data:
        if (
            not isinstance(key, str)
            or not key.isidentifier()
            or keyword.iskeyword(key)
        ):
            raise ValueError(f"top-level YAML key cannot be a dataclass field: {key!r}")
        if key == "benchmark_name":
            raise ValueError("source YAML uses reserved benchmark_name key")
        fields.append((key, Any))
    cls = dataclasses.make_dataclass(f"Real{format_name.title()}Document", fields)
    extension = FORMATS[format_name][0]
    return snapclass(
        "{self.benchmark_name}" + extension,
        stash=stash,
        manual=True,
        defaults=True,
    )(cls)


def _choose_scalar_path(
    data: dict[str, Any],
    state: _yaml_fast.YAMLState | None,
) -> tuple[str | int, ...]:
    if state is not None:
        candidates = [
            path
            for path in state.scalars
            if _is_supported_scalar(_get_data_value(data, path))
        ]
        if candidates:
            return min(candidates, key=lambda path: _scalar_score(_get_data_value(data, path), path))
    candidates = [
        path
        for path, value in _walk_values(data)
        if path and _is_supported_scalar(value)
    ]
    if not candidates:
        raise ValueError("source document has no replaceable scalar value")
    return min(candidates, key=lambda path: _scalar_score(_get_data_value(data, path), path))


def _choose_block_scalar_path(
    data: dict[str, Any],
    state: _yaml_fast.YAMLState | None,
) -> tuple[str | int, ...] | None:
    if state is None or not state.block_scalars:
        return None
    candidates = list(state.block_scalars)
    lengths = [len(_get_data_value(data, path)) for path in candidates]
    median_length = statistics.median(lengths)
    return min(
        candidates,
        key=lambda path: (
            abs(len(_get_data_value(data, path)) - median_length),
            len(path),
            _format_path(path),
        ),
    )


def _choose_list_path(data: dict[str, Any]) -> tuple[str | int, ...] | None:
    candidates = [path for path, value in _walk_values(data) if path and isinstance(value, list)]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda path: (
            len(_get_data_value(data, path)),
            len(path),
            _format_path(path),
        ),
    )


def _walk_values(
    value: Any,
    path: tuple[str | int, ...] = (),
) -> list[tuple[tuple[str | int, ...], Any]]:
    values = [(path, value)]
    if isinstance(value, Mapping):
        for key, child in value.items():
            values.extend(_walk_values(child, path + (key,)))
    elif _is_sequence(value):
        for index, child in enumerate(value):
            values.extend(_walk_values(child, path + (index,)))
    return values


def _is_supported_scalar(value: Any) -> bool:
    return (
        isinstance(value, (str, bool, int, float))
        and not (isinstance(value, float) and not _is_finite(value))
    )


def _scalar_score(value: Any, path: tuple[str | int, ...]) -> tuple[int, int, int]:
    if isinstance(value, bool):
        kind = 0
    elif isinstance(value, (int, float)):
        kind = 1
    else:
        kind = 2
    return kind, len(path), len(str(value))


def _scalar_variants(value: Any) -> tuple[Any, Any]:
    if isinstance(value, bool):
        return not value, value
    if isinstance(value, int) and not isinstance(value, bool):
        return value + 1, value
    if isinstance(value, float):
        return value + 0.125, value
    if isinstance(value, str):
        return value + "-benchmark", value
    raise ValueError(f"unsupported benchmark scalar: {type(value).__name__}")


def _block_scalar_variants(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError(f"unsupported block scalar: {type(value).__name__}")
    trailing_newlines = len(value) - len(value.rstrip("\n"))
    suffix = "\n" * trailing_newlines
    body = value[:-trailing_newlines] if trailing_newlines else value
    separator = "\n" if body else ""
    return body + separator + "snapclass benchmark edit" + suffix, value


def _get_data_value(data: Any, path: tuple[str | int, ...]) -> Any:
    value = data
    for part in path:
        value = value[part]
    return value


def _get_object_value(document: Any, path: tuple[str | int, ...]) -> Any:
    field_name = path[0]
    if not isinstance(field_name, str):
        raise ValueError("benchmark paths must start with a dataclass field")
    value = getattr(document, field_name)
    for part in path[1:]:
        value = value[part]
    return value


def _set_object_value(document: Any, path: tuple[str | int, ...], value: Any) -> None:
    if len(path) == 1:
        field_name = path[0]
        if not isinstance(field_name, str):
            raise ValueError("benchmark paths must start with a dataclass field")
        setattr(document, field_name, value)
        return
    parent = _get_object_value(document, path[:-1])
    parent[path[-1]] = value


def _format_path(path: tuple[str | int, ...]) -> str:
    text = ""
    for part in path:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += ("." if text else "") + part
    return text


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _is_finite(value: float) -> bool:
    return value == value and value not in {float("inf"), float("-inf")}


def _apply_ratios(metrics: list[FormatMetrics]) -> None:
    json_metrics = next(item for item in metrics if item.format == "json")
    for item in metrics:
        item.bytes_vs_json = round(item.bytes / json_metrics.bytes, 4)
        item.tokens_vs_json = round(item.tokens / json_metrics.tokens, 4)


def build_report(config: BenchmarkConfig, tokenizer: Any) -> BenchmarkReport:
    documents = []
    for source in config.sources:
        path = Path(source)
        print(f"benchmarking {path}", flush=True)
        documents.append(benchmark_document(path, tokenizer=tokenizer, config=config))
    return BenchmarkReport(
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        python=sys.version.split()[0],
        platform=platform.platform(),
        encoding=config.encoding,
        yaml_acceleration=YamlAcceleration(
            available=_yaml_fast.available(),
            dependencies=_yaml_fast.dependency_versions(),
        ),
        documents=documents,
    )


def print_report(report: BenchmarkReport) -> None:
    print()
    print(
        f"snapclass real-document benchmark | Python {report.python} | "
        f"{report.platform} | {report.encoding}"
    )
    for document in report.documents:
        status = "fast" if document.source_accelerated else "fallback"
        print()
        print(
            f"{document.name} | {document.source_bytes:,} source bytes | YAML {status} | "
            f"scalar={document.scalar_path} | block={document.block_scalar_path or '-'} | "
            f"n={document.iterations}"
        )
        print(
            "format    bytes  b/json   tokens  t/json  data_ms  text_ms  same_ms  "
            "scalar_ms  block_ms  struct_ms  get_ms  load_ms"
        )
        print("-" * 123)
        for item in document.formats:
            block = item.snapshot_save_block_scalar
            block_text = f"{block.median_ms:>8.3f}" if block else "       -"
            structural = item.snapshot_save_structural
            structural_text = f"{structural.median_ms:>9.3f}" if structural else "        -"
            print(
                f"{item.format:<6} {item.bytes:>9} {item.bytes_vs_json:>7.3f} "
                f"{item.tokens:>8} {item.tokens_vs_json:>7.3f} "
                f"{item.snapshot_data.median_ms:>8.3f} "
                f"{item.snapshot_text.median_ms:>8.3f} "
                f"{item.snapshot_save_unchanged.median_ms:>8.3f} "
                f"{item.snapshot_save_scalar.median_ms:>10.3f} "
                f"{block_text} "
                f"{structural_text} "
                f"{item.collection_get.median_ms:>7.3f} "
                f"{item.snapshot_load.median_ms:>8.3f}"
            )


def save_report(report: BenchmarkReport, output: str | None) -> None:
    if output is None:
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    sync(report, os.fspath(path.resolve()), manual=True)
    report.snapshot.save()
    print(f"\nwrote {path}")


def main() -> None:
    config = parse_args()
    tokenizer = load_tokenizer(config.encoding)
    report = build_report(config, tokenizer)
    print_report(report)
    save_report(report, config.output)


if __name__ == "__main__":
    main()
