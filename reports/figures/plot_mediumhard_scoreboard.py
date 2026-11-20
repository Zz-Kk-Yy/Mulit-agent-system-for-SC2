"""困难 15 有效局总览：4-6-5 vs 0-9-6，计入平局胜率。"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch

OUT_DIR = Path(__file__).resolve().parent

C_WIN = "#0072B2"
C_LOSS = "#D55E00"
C_TIE = "#8D8D8D"
C_TEXT = "#222222"
C_MUTED = "#555555"


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

    # 计入平局：4/15 = 26.7%，0/15 = 0.0%
    rows = [
        {"name": "自博弈", "w": 4, "l": 6, "t": 5, "rate": 26.7},
        {"name": "对照", "w": 0, "l": 9, "t": 6, "rate": 0.0},
    ]
    x = [0, 1]
    width = 0.58

    fig, ax = plt.subplots(figsize=(6.4, 8.0))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    for xi, row in zip(x, rows):
        bottom = 0.0
        parts = [
            (row["w"], C_WIN, "胜"),
            (row["l"], C_LOSS, "负"),
            (row["t"], C_TIE, "平"),
        ]
        for count, color, label in parts:
            if count <= 0:
                continue
            ax.bar(
                xi,
                count,
                bottom=bottom,
                width=width,
                color=color,
                edgecolor="white",
                linewidth=1.2,
            )
            mid = bottom + count / 2
            text = f"{count}{label}"
            if count >= 2:
                ax.text(
                    xi,
                    mid,
                    text,
                    ha="center",
                    va="center",
                    color="white",
                    fontsize=13,
                    fontweight="bold",
                )
            else:
                ax.text(
                    xi + width / 2 + 0.07,
                    mid,
                    text,
                    ha="left",
                    va="center",
                    color=color,
                    fontsize=12,
                    fontweight="bold",
                )
            bottom += count
        ax.text(
            xi,
            16.05,
            f"{row['rate']:.1f}%",
            ha="center",
            va="bottom",
            fontsize=22,
            color=C_TEXT,
            fontweight="bold",
            clip_on=False,
        )

    ax.annotate(
        "",
        xy=(0, 17.55),
        xytext=(1, 17.55),
        arrowprops={"arrowstyle": "-", "color": C_MUTED, "lw": 1.1},
        annotation_clip=False,
    )
    ax.text(
        0.5,
        17.72,
        "+26.7 pp",
        ha="center",
        va="bottom",
        fontsize=14,
        color=C_WIN,
        fontweight="bold",
    )

    ax.set_xlim(-0.55, 1.55)
    ax.set_ylim(0, 19.6)
    ax.set_xticks(x, [row["name"] for row in rows], fontsize=15, color=C_TEXT)
    ax.set_yticks(range(0, 16, 3))
    ax.set_ylabel("有效局（场）", fontsize=12, color=C_MUTED)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#cccccc")
    ax.spines["bottom"].set_color("#cccccc")
    ax.tick_params(axis="y", colors=C_MUTED)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#eeeeee", linewidth=0.8)

    ax.set_title(
        "中等困难 15 有效局  ·  计入平局胜率",
        loc="center",
        fontsize=15,
        color=C_TEXT,
        pad=16,
    )

    ax.legend(
        handles=[
            Patch(facecolor=C_WIN, edgecolor="none", label="我方胜"),
            Patch(facecolor=C_LOSS, edgecolor="none", label="对方胜"),
            Patch(facecolor=C_TIE, edgecolor="none", label="平局"),
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.08),
        ncol=3,
        frameon=False,
        fontsize=10,
    )

    fig.subplots_adjust(left=0.16, right=0.88, top=0.88, bottom=0.12)
    for ext in ("png", "pdf", "svg"):
        fig.savefig(OUT_DIR / f"mediumhard_scoreboard.{ext}")
    plt.close(fig)
    print("wrote", OUT_DIR / "mediumhard_scoreboard.png")


if __name__ == "__main__":
    main()
