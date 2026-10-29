"""Draw D8's sweep tables (docs/decisions.md) as one chart per threshold (#86).

    python3 tools/sweep_charts.py   # writes docs/diagrams/threshold-sweep.png; needs matplotlib

Each panel: the runs that met every mark (of 1,500) against the threshold's value, the value
kept marked, and the values with mishaps in red. It reads the tables, so the chart is always
what D8 says.
"""

import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NOTE = {"slow": "its marks compare\nagainst 0.3 itself:\nhigher values fail\nby definition"}
ROW = re.compile(r"^\| (\*\*)?([0-9.]+)(\*\*)?( \(now\))? \| (\d+) of (\d+) \| (\d+) \|")


def tables(text: str) -> dict[str, list[tuple[float, bool, int, int]]]:
    """{threshold: [(value, kept, runs passed, mishaps)]} from D8's markdown tables."""
    d8 = text[text.index("## D8.") :]
    out, name = {}, None
    for line in d8.splitlines():
        if m := re.match(r"^\| `(\w+)` \|", line):
            name = m.group(1)
            out[name] = []
        elif name and (m := ROW.match(line)):
            out[name].append(
                (float(m.group(2)), bool(m.group(4)), int(m.group(5)), int(m.group(7)))
            )
    return out


def main() -> int:
    data = tables((ROOT / "docs" / "decisions.md").read_text(encoding="utf-8"))
    fig, axes = plt.subplots(2, 4, figsize=(14, 6.5), sharey=True)
    for ax, (name, rows) in zip(axes.flat, data.items(), strict=False):
        x = [r[0] for r in rows]
        ax.plot(x, [r[2] for r in rows], "o-", color="#2f6fb0")
        for value, kept, passed, mishaps in rows:
            if kept:
                ax.plot(value, passed, "s", ms=11, mfc="none", mec="#1a1a1a", mew=1.6)
            if mishaps:
                ax.plot(value, passed, "o", color="#c0392b")
        if notes := [f"{m} at {v:g}" for v, _, _, m in rows if m]:
            ax.text(
                0.03,
                0.06,
                "mishaps: " + ", ".join(notes),
                transform=ax.transAxes,
                color="#c0392b",
                fontsize=8,
            )
        if name in NOTE:
            ax.text(0.55, 0.55, NOTE[name], transform=ax.transAxes, fontsize=8, color="#444444")
        ax.set_title(name, fontsize=11)
        ax.grid(alpha=0.3)
    for ax in axes.flat[len(data) :]:
        ax.axis("off")
    for ax in axes[:, 0]:
        ax.set_ylabel("runs that met every mark (of 1,500)")
    fig.suptitle(
        "Decision thresholds, one at a time, over 100 noisy runs of each scenario"
        " (square: the value kept, red: mishaps)",
        fontsize=11,
    )
    fig.tight_layout()
    out = ROOT / "docs" / "diagrams" / "threshold-sweep.png"
    fig.savefig(out, dpi=110)
    print(f"{out.relative_to(ROOT)}: {len(data)} thresholds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
