from __future__ import annotations

from tempfile import TemporaryDirectory

from snapclass import Stash, snapclass, sync


@snapclass
class ScoreRow:
    rank: int
    handle: str
    points: int
    qualified: bool


@snapclass
class Leaderboard:
    week: str
    scores: list[ScoreRow]
    published: bool = False


@snapclass
class LatencyPoint:
    minute: int
    p50_ms: float
    p95_ms: float
    healthy: bool


@snapclass
class RunReport:
    name: str
    service: str
    tags: list[str]
    points: list[LatencyPoint]


def main() -> None:
    with TemporaryDirectory(prefix="snapclass-terse-examples-") as root:
        stash = Stash(root)

        board = Leaderboard(
            "week-32",
            scores=[
                ScoreRow(1, "mira", 982, True),
                ScoreRow(2, "jon-vale", 941, True),
                ScoreRow(3, "noor", 917, False),
            ],
            published=True,
        )
        sync(board, "{self.week}.terse", stash=stash, manual=True, defaults=True)
        board.save()

        report = RunReport(
            "checkout-baseline",
            service="checkout",
            tags=["prod", "latency", "baseline"],
            points=[
                LatencyPoint(0, 124.5, 230.2, True),
                LatencyPoint(5, 126.1, 235.8, True),
                LatencyPoint(10, 190.4, 410.7, False),
            ],
        )
        sync(report, "{self.name}.terse", stash=stash, manual=True, defaults=True)
        report.save()

        leaderboard_text = board.snapshot.path.read_text(encoding="utf-8")
        report_text = report.snapshot.path.read_text(encoding="utf-8")

        assert "#[rank handle points qualified]" in leaderboard_text
        assert "#[minute p50_ms p95_ms healthy]" in report_text

        print("week-32.terse")
        print(leaderboard_text, end="")
        print("checkout-baseline.terse")
        print(report_text, end="")


if __name__ == "__main__":
    main()
