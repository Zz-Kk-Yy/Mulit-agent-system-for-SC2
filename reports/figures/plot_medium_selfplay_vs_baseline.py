"""中等 15 有效局：自博弈 13-1-1 vs 无自博弈 9-4-2。胜利时间图 + 胜率图。"""

from __future__ import annotations

from pathlib import Path
import random

import matplotlib.pyplot as plt
from matplotlib import font_manager, ticker
OUT_DIR = Path(__file__).resolve().parent

# 对局时钟 MM:SS；仅「我方胜」进入胜利时间图。顺序 = 有效局序号。
SELFPLAY_WINS = [
    (1, "12:13"),
    (2, "14:39"),
    (3, "13:30"),
    (4, "11:12"),
    (5, "10:07"),
    (6, "09:45"),
    (7, "12:36"),
    (8, "07:57"),
    (9, "09:36"),
    (10, "12:12"),
    (11, "11:13"),
    (13, "09:35"),
    (14, "09:48"),
]
BASELINE_WINS = [
    (3, "12:39"),
    (6, "10:49"),
    (7, "11:31"),
    (8, "20:47"),
    (9, "16:45"),
    (10, "09:22"),
    (13, "11:37"),
    (14, "12:18"),
    (15, "12:58"),
]
# 有效局结果，用于累计胜率（计入平局 = 累计胜 / 已打有效局）
SELFPLAY_OUTCOMES = list("WWWWWWWWWWWTWWL")
BASELINE_OUTCOMES = list("LTWLLWWWWWT LWWW".replace(" ", ""))

C_SELF = "#0072B2"
C_BASE = "#D55E00"
LABEL_SELF = "自博弈 13-1-1"
LABEL_BASE = "无自博弈 9-4-2"


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


def setup_style() -> None:
    font_name = pick_cjk_font()
    plt.rcParams.update(
        {
            "font.family": font_name,
            "font.size": 10,
            "axes.unicode_minus": False,
            "axes.linewidth": 0.8,
            "axes.labelsize": 11,
            "axes.titlesize": 12,
            "figure.dpi": 140,
            "savefig.dpi": 220,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.08,
            "legend.frameon": False,
            "legend.fontsize": 9,
        }
    )


def cumulative_win_rate(outcomes: list[str]) -> list[float]:
    wins = 0
    rates: list[float] = []
    for i, result in enumerate(outcomes, start=1):
        if result == "W":
            wins += 1
        rates.append(100.0 * wins / i)
    return rates


def style_ax(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="#dddddd", linewidth=0.6)
    ax.set_axisbelow(True)


def plot_win_time() -> None:
    self_x = [g for g, _ in SELFPLAY_WINS]
    self_y = [mmss_to_min(t) for _, t in SELFPLAY_WINS]
    base_x = [g for g, _ in BASELINE_WINS]
    base_y = [mmss_to_min(t) for _, t in BASELINE_WINS]
    self_mean = sum(self_y) / len(self_y)
    base_mean = sum(base_y) / len(base_y)

    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.4), width_ratios=[1.35, 1])

    ax = axes[0]
    ax.scatter(
        self_x,
        self_y,
        s=46,
        c=C_SELF,
        zorder=3,
        label=f"{LABEL_SELF}（均值 {min_to_mmss(self_mean)}）",
    )
    ax.scatter(
        base_x,
        base_y,
        s=46,
        c=C_BASE,
        marker="s",
        zorder=3,
        label=f"{LABEL_BASE}（均值 {min_to_mmss(base_mean)}）",
    )
    ax.axhline(self_mean, color=C_SELF, ls="--", lw=1.0, alpha=0.85)
    ax.axhline(base_mean, color=C_BASE, ls="--", lw=1.0, alpha=0.85)
    ax.set_xlim(0.5, 15.5)
    ax.set_xticks(range(1, 16))
    ax.set_xlabel("有效局序号")
    ax.set_ylabel("胜利时的对局时钟")
    ax.set_title("A  各场胜利的对局时钟")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: min_to_mmss(v)))
    ax.set_ylim(7, 22)
    style_ax(ax)
    ax.legend(loc="upper left")

    ax = axes[1]
    ax.boxplot(
        [self_y, base_y],
        positions=[1, 2],
        widths=0.45,
        patch_artist=True,
        showfliers=False,
        medianprops={"color": "#222222", "lw": 1.2},
        boxprops={"facecolor": "#ffffff", "edgecolor": "#666666"},
        whiskerprops={"color": "#666666"},
        capprops={"color": "#666666"},
    )
    rnd = random.Random(0)
    ax.scatter(
        [1 + rnd.uniform(-0.08, 0.08) for _ in self_y],
        self_y,
        s=36,
        c=C_SELF,
        zorder=3,
        alpha=0.85,
    )
    ax.scatter(
        [2 + rnd.uniform(-0.08, 0.08) for _ in base_y],
        base_y,
        s=36,
        c=C_BASE,
        marker="s",
        zorder=3,
        alpha=0.85,
    )
    ax.scatter([1], [self_mean], marker="D", s=42, c=C_SELF, zorder=4, edgecolors="white", linewidths=0.6)
    ax.scatter([2], [base_mean], marker="D", s=42, c=C_BASE, zorder=4, edgecolors="white", linewidths=0.6)
    ax.set_xticks([1, 2], [f"{LABEL_SELF}\nn={len(self_y)}", f"{LABEL_BASE}\nn={len(base_y)}"])
    ax.set_ylabel("胜利时的对局时钟")
    ax.set_title("B  胜利时钟分布（菱形=均值）")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: min_to_mmss(v)))
    ax.set_ylim(7, 22)
    style_ax(ax)
    ax.grid(axis="x", visible=False)

    fig.suptitle("中等 PvE · 胜利时间（仅我方胜；对局时钟，不是墙钟）", fontsize=13, y=1.02)
    fig.tight_layout()
    for ext in ("png", "pdf", "svg"):
        fig.savefig(OUT_DIR / f"medium_win_time.{ext}")
    plt.close(fig)


def plot_win_rate() -> None:
    self_cum = cumulative_win_rate(SELFPLAY_OUTCOMES)
    base_cum = cumulative_win_rate(BASELINE_OUTCOMES)
    games = list(range(1, 16))

    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.4), width_ratios=[1, 1.25])

    ax = axes[0]
    cats = ["计入平局\n胜 / 15", "不计平局\n胜 / (15−平)"]
    self_rates = [13 / 15 * 100, 13 / 14 * 100]
    base_rates = [9 / 15 * 100, 9 / 13 * 100]
    x = [0, 1]
    w = 0.34
    b1 = ax.bar([i - w / 2 for i in x], self_rates, width=w, color=C_SELF, label=LABEL_SELF)
    b2 = ax.bar([i + w / 2 for i in x], base_rates, width=w, color=C_BASE, label=LABEL_BASE)
    ax.set_xticks(x, cats)
    ax.set_ylabel("胜率（%）")
    ax.set_ylim(0, 100)
    ax.set_title("A  终局胜率", pad=28)
    style_ax(ax)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.02), ncol=2, borderaxespad=0, fontsize=8)
    for bars in (b1, b2):
        for rect in bars:
            h = rect.get_height()
            ax.text(
                rect.get_x() + rect.get_width() / 2,
                min(h + 1.4, 98),
                f"{h:.1f}%",
                ha="center",
                va="bottom",
                fontsize=9,
            )

    ax = axes[1]
    ax.plot(games, self_cum, color=C_SELF, marker="o", ms=4.5, lw=1.6, label=LABEL_SELF)
    ax.plot(games, base_cum, color=C_BASE, marker="s", ms=4.5, lw=1.6, label=LABEL_BASE)
    ax.axhline(self_cum[-1], color=C_SELF, ls=":", lw=0.9, alpha=0.7)
    ax.axhline(base_cum[-1], color=C_BASE, ls=":", lw=0.9, alpha=0.7)
    ax.set_xlim(0.7, 16.2)
    ax.set_xticks(games)
    ax.set_xlabel("有效局序号")
    ax.set_ylabel("累计胜率（计入平局，%）")
    ax.set_ylim(0, 105)
    ax.set_title("B  随有效局推进的累计胜率")
    style_ax(ax)
    ax.legend(loc="lower right")
    ax.text(15.2, self_cum[-1] + 2.4, f"{self_cum[-1]:.1f}%", color=C_SELF, fontsize=8, va="bottom")
    ax.text(15.2, base_cum[-1] - 2.4, f"{base_cum[-1]:.1f}%", color=C_BASE, fontsize=8, va="top")

    fig.suptitle("中等 PvE · 胜率（人族电脑 Medium，各 15 场有效局）", fontsize=13, y=1.02)
    fig.tight_layout()
    for ext in ("png", "pdf", "svg"):
        fig.savefig(OUT_DIR / f"medium_win_rate.{ext}")
    plt.close(fig)


def main() -> None:
    setup_style()
    plot_win_time()
    plot_win_rate()
    print("wrote", OUT_DIR / "medium_win_time.png")
    print("wrote", OUT_DIR / "medium_win_rate.png")


if __name__ == "__main__":
    main()
