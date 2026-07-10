from __future__ import annotations

import argparse
import html
import os
import platform
import statistics
import sys
import tempfile
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.is_dir():
    sys.path.insert(0, os.fspath(SRC))

from snapclass import Fresh, Stash, formatters, snapclass, sync  # noqa: E402
from snapclass import _yaml_fast  # noqa: E402


FORMATS = ("yaml", "terse", "json")
EXTENSIONS = {"yaml": ".yml", "terse": ".terse", "json": ".json"}
REQUEST_SAMPLE_COUNT = 120


@snapclass
class BenchmarkConfig:
    iterations: int = 50
    warmups: int = 5
    encoding: str = "cl100k_base"
    output: str | None = None


@snapclass
class PayloadStyle:
    voice: str
    temperature: float
    model: str
    tools: list[str] = Fresh.List


@snapclass
class PayloadStep:
    index: int
    slug: str
    title: str
    enabled: bool
    weight: float
    tags: list[str] = Fresh.List
    metadata: dict[str, object] = Fresh.Dict
    note: str | None = None


@snapclass
class PayloadRequestSample:
    minute: int
    route: str
    worker: str
    status: str
    p50_ms: float
    p95_ms: float
    tokens_in: int
    tokens_out: int
    cache_hit: bool
    cost_micros: int


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
    snapshot_data: OperationMetrics
    formatter_dump: OperationMetrics
    formatter_load: OperationMetrics
    snapshot_text: OperationMetrics
    snapshot_save_unchanged: OperationMetrics
    snapshot_save_scalar: OperationMetrics
    snapshot_save_structural: OperationMetrics
    automatic_assignment: OperationMetrics
    collection_get: OperationMetrics
    snapshot_load: OperationMetrics


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
    iterations: int
    warmups: int
    schema_array_rows: int
    yaml_acceleration: YamlAcceleration
    formats: list[FormatMetrics]


def document_class(
    format_name: str,
    *,
    manual: bool = True,
    stash: Stash | None = None,
) -> type:
    extension = EXTENSIONS[format_name]

    @snapclass(
        "{self.name}" + extension,
        manual=manual,
        defaults=True,
        stash=stash,
    )
    class BenchmarkDocument:
        name: str
        title: str
        summary: str
        style: PayloadStyle
        steps: list[PayloadStep]
        request_samples: list[PayloadRequestSample]
        metrics: dict[str, float]
        flags: dict[str, bool]
        aliases: list[str]
        optional_note: str | None = None

    BenchmarkDocument.__name__ = f"Benchmark{format_name.title()}Document"
    return BenchmarkDocument


def parse_args() -> BenchmarkConfig:
    parser = argparse.ArgumentParser(
        description="Compare snapclass YAML, TERSE, and JSON serialization.",
    )
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--warmups", type=int, default=5)
    parser.add_argument("--encoding", default="cl100k_base")
    parser.add_argument("--output")
    args = parser.parse_args()
    config = BenchmarkConfig(
        iterations=args.iterations,
        warmups=args.warmups,
        encoding=args.encoding,
        output=args.output,
    )
    if config.iterations <= 0:
        raise SystemExit("--iterations must be greater than zero")
    if config.warmups < 0:
        raise SystemExit("--warmups must be zero or greater")
    if config.output is not None and Path(config.output).suffix.lower() not in {
        ".yml",
        ".yaml",
    }:
        raise SystemExit("--output must be a .yml or .yaml path")
    return config


def load_tokenizer(encoding_name: str) -> Any:
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"urllib3 .* doesn't match a supported version!",
                category=Warning,
                module=r"requests",
            )
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


def payload_kwargs() -> dict[str, Any]:
    steps = [
        PayloadStep(
            index=index,
            slug=f"step-{index:02d}",
            title=f"Review batch {index}",
            enabled=index % 3 != 0,
            weight=round(0.55 + index * 0.075, 3),
            tags=["review", "format", "benchmark", f"group-{index % 4}"],
            metadata={
                "priority": index % 5,
                "owner": f"team-{index % 3}",
                "retry": index % 2 == 0,
                "threshold": round(0.81 + index * 0.003, 3),
            },
            note=None if index % 4 else f"Optional note for batch {index}.",
        )
        for index in range(1, 25)
    ]
    routes = [
        "/checkout/cart",
        "/checkout/pay",
        "/catalog/search",
        "/account/profile",
    ]
    statuses = ["ok", "ok", "ok", "slow"]
    request_samples = [
        PayloadRequestSample(
            minute=index,
            route=routes[index % len(routes)],
            worker=f"edge-{index % 8:02d}",
            status=statuses[index % len(statuses)],
            p50_ms=round(92.0 + (index % 17) * 3.2 + (index // 24) * 1.1, 1),
            p95_ms=round(180.0 + (index % 23) * 5.4 + (index // 18) * 2.3, 1),
            tokens_in=820 + (index % 11) * 37,
            tokens_out=260 + (index % 7) * 19,
            cache_hit=index % 5 != 0,
            cost_micros=440 + (index % 13) * 29,
        )
        for index in range(REQUEST_SAMPLE_COUNT)
    ]
    return {
        "title": "Formatter Benchmark Fixture",
        "summary": (
            "Representative nested snapclass payload used to compare readable "
            "file formats for size, tokens, common snapshot operations, and "
            "large repeated primitive rows."
        ),
        "style": PayloadStyle(
            voice="concise",
            temperature=0.35,
            model="gpt-5-benchmark",
            tools=["snapshot.text", "snapshot.save", "collection.get", "snapshot.load"],
        ),
        "steps": steps,
        "request_samples": request_samples,
        "metrics": {
            "accuracy": 0.982,
            "loss": 0.037,
            "latency_p50": 124.5,
            "latency_p95": 230.25,
        },
        "flags": {
            "stable": True,
            "human_editable": True,
            "include_defaults": True,
        },
        "aliases": ["yaml-vs-json", "terse-vs-json", "formatter-regression"],
        "optional_note": None,
    }


def new_document(cls: type, format_name: str, *, name: str | None = None) -> Any:
    return cls(name or f"sample-{format_name}", **payload_kwargs())


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


def benchmark_format(
    format_name: str,
    *,
    stash: Stash,
    tokenizer: Any,
    config: BenchmarkConfig,
) -> FormatMetrics:
    cls = document_class(format_name)
    document = new_document(cls, format_name)
    sync(document, "{self.name}" + EXTENSIONS[format_name], stash=stash, manual=True)
    path = document.snapshot.path
    if path is None:
        raise RuntimeError("benchmark document did not resolve a snapshot path")

    formatter = formatters.formatter_for(
        path,
        formatters=stash.effective_formatters(),
    )

    data_metrics = measure(
        lambda: document.snapshot.data,
        warmups=config.warmups,
        iterations=config.iterations,
    )
    data = document.snapshot.data
    text = formatter.dumps(data)

    dump_metrics = measure(
        lambda: formatter.dumps(data),
        warmups=config.warmups,
        iterations=config.iterations,
    )
    formatter_load_metrics = measure(
        lambda: formatter.loads(text),
        warmups=config.warmups,
        iterations=config.iterations,
    )

    def render_text() -> str:
        return document.snapshot.text

    text_metrics = measure(
        render_text,
        warmups=config.warmups,
        iterations=config.iterations,
    )

    document.snapshot.save()
    document.snapshot.load()
    if format_name == "yaml" and _yaml_fast.available():
        if document.snapshot._yaml_state is None:
            raise RuntimeError(
                "representative YAML document did not enter the yaml-fast path"
            )
    unchanged_save_metrics = measure(
        document.snapshot.save,
        warmups=config.warmups,
        iterations=config.iterations,
    )

    text = path.read_text(encoding="utf-8")

    scalar_document = new_document(
        cls,
        format_name,
        name=f"sample-{format_name}-scalar",
    )
    sync(
        scalar_document,
        "{self.name}" + EXTENSIONS[format_name],
        stash=stash,
        manual=True,
    )
    scalar_document.snapshot.save()
    scalar_document.snapshot.load()
    scalar_toggle = False

    def save_scalar() -> None:
        nonlocal scalar_toggle
        scalar_toggle = not scalar_toggle
        scalar_document.style.temperature = 0.35 if scalar_toggle else 0.45
        scalar_document.snapshot.save()

    scalar_save_metrics = measure(
        save_scalar,
        warmups=config.warmups,
        iterations=config.iterations,
    )

    structural_document = new_document(
        cls,
        format_name,
        name=f"sample-{format_name}-structural",
    )
    sync(
        structural_document,
        "{self.name}" + EXTENSIONS[format_name],
        stash=stash,
        manual=True,
    )
    structural_document.snapshot.save()
    structural_document.snapshot.load()
    extra_step = PayloadStep(
        index=99,
        slug="step-99",
        title="Structural benchmark row",
        enabled=True,
        weight=1.0,
        tags=["benchmark"],
        metadata={"owner": "benchmark"},
    )

    def save_structural() -> None:
        if structural_document.steps[-1].index == 99:
            structural_document.steps.pop()
        else:
            structural_document.steps.append(extra_step)
        structural_document.snapshot.save()

    structural_save_metrics = measure(
        save_structural,
        warmups=config.warmups,
        iterations=config.iterations,
    )

    automatic_cls = document_class(format_name, manual=False, stash=stash)
    automatic_document = new_document(
        automatic_cls,
        format_name,
        name=f"sample-{format_name}-automatic",
    )
    automatic_toggle = False

    def assign_automatic_scalar() -> None:
        nonlocal automatic_toggle
        automatic_toggle = not automatic_toggle
        automatic_document.title = (
            "Formatter Benchmark Fixture A"
            if automatic_toggle
            else "Formatter Benchmark Fixture B"
        )

    automatic_assignment_metrics = measure(
        assign_automatic_scalar,
        warmups=config.warmups,
        iterations=config.iterations,
    )

    collection = cls.snapshots(stash)
    get_metrics = measure(
        lambda: collection.get(f"sample-{format_name}"),
        warmups=config.warmups,
        iterations=config.iterations,
    )

    load_document = new_document(cls, format_name)
    sync(load_document, "{self.name}" + EXTENSIONS[format_name], stash=stash, manual=True)
    snapshot_load_metrics = measure(
        load_document.snapshot.load,
        warmups=config.warmups,
        iterations=config.iterations,
    )

    return FormatMetrics(
        format=format_name,
        extension=EXTENSIONS[format_name],
        bytes=len(text.encode("utf-8")),
        bytes_vs_json=1.0,
        tokens=len(tokenizer.encode(text)),
        tokens_vs_json=1.0,
        snapshot_data=data_metrics,
        formatter_dump=dump_metrics,
        formatter_load=formatter_load_metrics,
        snapshot_text=text_metrics,
        snapshot_save_unchanged=unchanged_save_metrics,
        snapshot_save_scalar=scalar_save_metrics,
        snapshot_save_structural=structural_save_metrics,
        automatic_assignment=automatic_assignment_metrics,
        collection_get=get_metrics,
        snapshot_load=snapshot_load_metrics,
    )


def with_ratios(metrics: list[FormatMetrics]) -> list[FormatMetrics]:
    by_format = {item.format: item for item in metrics}
    json_metrics = by_format["json"]
    for item in metrics:
        item.bytes_vs_json = round(item.bytes / json_metrics.bytes, 4)
        item.tokens_vs_json = round(item.tokens / json_metrics.tokens, 4)
    return metrics


def build_report(config: BenchmarkConfig, tokenizer: Any) -> BenchmarkReport:
    with tempfile.TemporaryDirectory(prefix="snapclass-format-benchmark-") as root:
        stash = Stash(root)
        metrics = [
            benchmark_format(
                format_name,
                stash=stash,
                tokenizer=tokenizer,
                config=config,
            )
            for format_name in FORMATS
        ]
    return BenchmarkReport(
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        python=sys.version.split()[0],
        platform=platform.platform(),
        encoding=config.encoding,
        iterations=config.iterations,
        warmups=config.warmups,
        schema_array_rows=REQUEST_SAMPLE_COUNT,
        yaml_acceleration=YamlAcceleration(
            available=_yaml_fast.available(),
            dependencies=_yaml_fast.dependency_versions(),
        ),
        formats=with_ratios(metrics),
    )


def print_report(report: BenchmarkReport) -> None:
    print(
        f"snapclass format benchmark | python {report.python} | "
        f"{report.platform} | encoding {report.encoding}"
    )
    print(f"iterations={report.iterations} warmups={report.warmups} at {report.timestamp}")
    yaml_status = "available" if report.yaml_acceleration.available else "unavailable"
    dependencies = ", ".join(
        f"{name}={version}"
        for name, version in report.yaml_acceleration.dependencies.items()
    )
    print(f"yaml-fast={yaml_status}" + (f" | {dependencies}" if dependencies else ""))
    print(f"flat request sample rows={report.schema_array_rows}")
    print()
    print(
        "format  bytes  b/json  tokens  t/json  text_ms  same_ms  scalar_ms  "
        "struct_ms  auto_ms  get_ms  load_ms"
    )
    print("-" * 119)
    for item in report.formats:
        print(
            f"{item.format:<6} "
            f"{item.bytes:>6} "
            f"{item.bytes_vs_json:>7.3f} "
            f"{item.tokens:>7} "
            f"{item.tokens_vs_json:>7.3f} "
            f"{item.snapshot_text.median_ms:>8.4f} "
            f"{item.snapshot_save_unchanged.median_ms:>8.4f} "
            f"{item.snapshot_save_scalar.median_ms:>10.4f} "
            f"{item.snapshot_save_structural.median_ms:>10.4f} "
            f"{item.automatic_assignment.median_ms:>8.4f} "
            f"{item.collection_get.median_ms:>7.4f} "
            f"{item.snapshot_load.median_ms:>8.4f}"
        )
    print()
    print("format  data_ms  dump_ms  load_ms")
    print("-" * 36)
    for item in report.formats:
        print(
            f"{item.format:<6} "
            f"{item.snapshot_data.median_ms:>7.4f} "
            f"{item.formatter_dump.median_ms:>7.4f} "
            f"{item.formatter_load.median_ms:>8.4f}"
        )


def save_report(report: BenchmarkReport, output: str | None) -> None:
    if output is None:
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    sync(report, os.fspath(path.resolve()), manual=True)
    report.snapshot.save()
    svg_path = path.with_suffix(".svg")
    svg_path.write_text(render_svg(report), encoding="utf-8")
    print()
    print(f"wrote {path}")
    print(f"wrote {svg_path}")


def render_svg(report: BenchmarkReport) -> str:
    panels = [
        ("Bytes", lambda item: float(item.bytes), "bytes"),
        ("Tokens", lambda item: float(item.tokens), "tokens"),
        ("snapshot.data median", lambda item: item.snapshot_data.median_ms, "ms"),
        ("formatter.dumps median", lambda item: item.formatter_dump.median_ms, "ms"),
        ("formatter.loads median", lambda item: item.formatter_load.median_ms, "ms"),
        ("snapshot.text median", lambda item: item.snapshot_text.median_ms, "ms"),
        (
            "snapshot.save unchanged median",
            lambda item: item.snapshot_save_unchanged.median_ms,
            "ms",
        ),
        (
            "snapshot.save nested scalar median",
            lambda item: item.snapshot_save_scalar.median_ms,
            "ms",
        ),
        (
            "snapshot.save structural median",
            lambda item: item.snapshot_save_structural.median_ms,
            "ms",
        ),
        (
            "automatic scalar assignment median",
            lambda item: item.automatic_assignment.median_ms,
            "ms",
        ),
        ("collection.get median", lambda item: item.collection_get.median_ms, "ms"),
        ("snapshot.load median", lambda item: item.snapshot_load.median_ms, "ms"),
    ]
    width = 1040
    panel_height = 150
    margin_x = 150
    top = 96
    gap = 22
    height = top + len(panels) * panel_height + (len(panels) - 1) * gap + 46
    chart_width = width - margin_x - 80
    colors = {"yaml": "#1f6feb", "terse": "#2da44e", "json": "#bf8700"}

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        'aria-labelledby="title desc">',
        "<title>snapclass format benchmark results</title>",
        (
            '<desc id="desc">Bar chart comparing YAML, TERSE, and JSON benchmark '
            "bytes, tokens, and median operation times.</desc>"
        ),
        "<style>",
        "text{font-family:Segoe UI,Public Sans,system-ui,sans-serif;fill:#24292f}",
        ".title{font-size:22px;font-weight:700}",
        ".meta{font-size:12px;fill:#57606a}",
        ".note{font-size:12px;fill:#57606a;font-weight:600}",
        ".label{font-size:13px;font-weight:600}",
        ".tick{font-size:11px;fill:#57606a}",
        ".value{font-size:11px;fill:#24292f}",
        ".axis{stroke:#d0d7de;stroke-width:1}",
        ".grid{stroke:#eaeef2;stroke-width:1}",
        "</style>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text id="title" class="title" x="32" y="34">snapclass format benchmark</text>',
        (
            f'<text class="meta" x="32" y="56">'
            f'{_svg_text(report.timestamp)} | Python {_svg_text(report.python)} | '
            f'{_svg_text(report.encoding)} | iterations={report.iterations} '
            f'warmups={report.warmups}</text>'
        ),
        (
            '<text class="note" x="32" y="74">YAML accelerator: '
            f'{"available" if report.yaml_acceleration.available else "unavailable"}'
            f' | flat rows: {report.schema_array_rows}'
            "</text>"
        ),
    ]

    for panel_index, (title, getter, unit) in enumerate(panels):
        y = top + panel_index * (panel_height + gap)
        values = [(item.format, getter(item)) for item in report.formats]
        max_value = max(value for _, value in values) or 1.0
        scale_max = max_value * 1.12
        parts.extend(_svg_panel(title, values, unit, y, margin_x, chart_width, scale_max, colors))

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _svg_panel(
    title: str,
    values: list[tuple[str, float]],
    unit: str,
    y: int,
    margin_x: int,
    chart_width: int,
    scale_max: float,
    colors: dict[str, str],
) -> list[str]:
    axis_y = y + 126
    bar_height = 24
    bar_gap = 10
    lines = [
        f'<text class="label" x="32" y="{y + 16}">{_svg_text(title)}</text>',
        f'<line class="axis" x1="{margin_x}" y1="{axis_y}" '
        f'x2="{margin_x + chart_width}" y2="{axis_y}"/>',
    ]
    for index in range(1, 5):
        x = margin_x + chart_width * index / 4
        tick = scale_max * index / 4
        lines.append(
            f'<line class="grid" x1="{x:.1f}" y1="{y + 24}" x2="{x:.1f}" y2="{axis_y}"/>'
        )
        lines.append(
            f'<text class="tick" x="{x - 16:.1f}" y="{axis_y + 17}">'
            f'{_format_number(tick)}</text>'
        )

    for index, (name, value) in enumerate(values):
        bar_y = y + 28 + index * (bar_height + bar_gap)
        width = chart_width * value / scale_max
        color = colors.get(name, "#57606a")
        lines.extend(
            [
                f'<text class="tick" x="72" y="{bar_y + 17}">{_svg_text(name)}</text>',
                f'<rect x="{margin_x}" y="{bar_y}" width="{width:.1f}" height="{bar_height}" '
                f'rx="4" fill="{color}"/>',
                f'<text class="value" x="{margin_x + width + 8:.1f}" y="{bar_y + 17}">'
                f'{_format_number(value)} {_svg_text(unit)}</text>',
            ]
        )
    return lines


def _format_number(value: float) -> str:
    if value >= 100:
        return str(int(round(value)))
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _svg_text(value: object) -> str:
    return html.escape(str(value), quote=True)


def main() -> int:
    config = parse_args()
    tokenizer = load_tokenizer(config.encoding)
    report = build_report(config, tokenizer)
    print_report(report)
    save_report(report, config.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
