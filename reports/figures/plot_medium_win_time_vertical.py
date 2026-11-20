"""中等 15 有效局：自博弈 vs 对照的胜利对局时钟（竖栏）。"""

from __future__ import annotations

from pathlib import Path
import random

import matplotlib.pyplot as plt
from matplotlib import font_manager, ticker
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

OUT_DIR = Path(__file__).resolve().parent

C_SELF = "#0072B2"
C_BASE = "#D55E00"
C_TEXT = "#222222"
C_MUTED = "#555555"

SELFPLAY_WINS = [
    "12:13",
    "14:39",
    "13:30",
    "11:12",
    "10:07",
    "09:45",
    "12:36",
    "07:57",
    "09:36",
    "12:12",
    "11:13",
    "09:35",
    "09:48",
]
BASELINE_WINS = [
    "12:39",
    "10:49",
    "11:31",
    "20:47",
    "16:45",
    "09:22",
    "11:37",
    "12:18",
    "12:58",
]


def mmss_to_min(text: str) -> float:
    minute, second = text.split(":")
    return int(minute) + int(second) / 60.0


def min_to_mmss(value: float) -> str:
    total = int(round(value * 60))
    return f"{total // 60:02d}:{total % 60:02d}"


def pick_cjk_font() -> str:
    wanted = ("Microsoft YaHei", "Microsoft YaHei UI", "SimHei", "Noto Sans CJK SC")
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in wanted:
        if name in available:
            return name
    for path in (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    ):
        if path.exists():
            font_manager.fontManager.addfont(str(path))
            return font_manager.FontProperties(fname=str(path)).get_name()
    return "DejaVu Sans"


def main() -> None:
    font_name = pick_cjk_font()
    plt.rcParams.update(
        {
            "font.family": font_name,
            "font.size": 11,
            "axes.unicode_minus": False,
            "savefig.dpi": 220,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.12,
        }
    )

    groups = [
        {"name": "自博弈", "times": [mmss_to_min(t) for t in SELFPLAY_WINS], "color": C_SELF},
        {"name": "对照", "times": [mmss_to_min(t) for t in BASELINE_WINS], "color": C_BASE},
    ]
    for row in groups:
        row["mean"] = sum(row["times"]) / len(row["times"])

    x = [0, 1]
    width = 0.58
    rnd = random.Random(0)

    fig, ax = plt.subplots(figsize=(6.4, 8.0))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    for xi, row in zip(x, groups):
        ax.bar(
            xi,
            row["mean"],
            width=width,
            color=row["color"],
            edgecolor="white",
            linewidth=1.2,
            alpha=0.92,
            zorder=2,
        )
        jittered = [xi + rnd.uniform(-0.14, 0.14) for _ in row["times"]]
        ax.scatter(
            jittered,
            row["times"],
            s=42,
            c="white",
            edgecolors=row["color"],
            linewidths=1.2,
            zorder=3,
        )
        ax.scatter(
            [xi],
            [row["mean"]],
            s=70,
            marker="D",
            c=row["color"],
            edgecolors="white",
            linewidths=0.8,
            zorder=4,
        )
        ax.text(
            xi,
            22.35,
            min_to_mmss(row["mean"]),
            ha="center",
            va="bottom",
            fontsize=22,
            color=C_TEXT,
            fontweight="bold",
            clip_on=False,
        )

    ax.annotate(
        "",
        xy=(0, 23.85),
        xytext=(1, 23.85),
        arrowprops={"arrowstyle": "-", "color": C_MUTED, "lw": 1.1},
        annotation_clip=False,
    )
    ax.text(
        0.5,
        24.02,
        "早 02:06",
        ha="center",
        va="bottom",
        fontsize=14,
        color=C_SELF,
        fontweight="bold",
    )

    ax.set_xlim(-0.55, 1.55)
    ax.set_ylim(0, 26.2)
    ax.set_xticks(x, [row["name"] for row in groups], fontsize=15, color=C_TEXT)
    ax.set_ylabel("胜利对局时钟", fontsize=12, color=C_MUTED)
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: min_to_mmss(v)))
    ax.set_yticks(range(0, 22, 3))
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#cccccc")
    ax.spines["bottom"].set_color("#cccccc")
    ax.tick_params(axis="y", colors=C_MUTED)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#eeeeee", linewidth=0.8)

    ax.set_title(
        "中等 15 有效局  ·  胜利对局时钟",
        loc="center",
        fontsize=15,
        color=C_TEXT,
        pad=16,
    )

    ax.legend(
        handles=[
            Patch(facecolor=C_SELF, edgecolor="none", label="均值（柱）"),
            Line2D(
                [0],
                [0],
                marker="o",
                color="none",
                markerfacecolor="white",
                markeredgecolor=C_MUTED,
                markeredgewidth=1.2,
                markersize=7,
                label="各场胜利",
            ),
            Line2D(
                [0],
                [0],
                marker="D",
                color="none",
                markerfacecolor=C_MUTED,
                markeredgecolor="white",
                markersize=7,
                label="组内均值",
            ),
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.08),
        ncol=3,
        frameon=False,
        fontsize=10,
    )

    fig.subplots_adjust(left=0.20, right=0.88, top=0.88, bottom=0.12)
    for ext in ("png", "pdf", "svg"):
        fig.savefig(OUT_DIR / f"medium_win_time_bars.{ext}")
    plt.close(fig)
    print("wrote", OUT_DIR / "medium_win_time_bars.png")


if __name__ == "__main__":
    main()
