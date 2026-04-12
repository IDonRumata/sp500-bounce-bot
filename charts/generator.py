"""
Generate statistics charts as PNG images for Telegram.
Uses matplotlib — no LLM involved, pure code calculations.
"""
import io
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # non-interactive backend, must be set before importing pyplot
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from config import SUCCESS_THRESHOLD_PCT, FAILURE_THRESHOLD_PCT, logger


def generate_stats_chart(stats: dict) -> bytes | None:
    """
    Generate a 4-panel statistics chart.
    Returns PNG bytes ready to send via Telegram, or None on error.

    Panels:
    1. Win/Loss/Neutral donut (by status: success/neutral/failure)
    2. Returns distribution histogram
    3. Cumulative P&L line over time
    4. Score band → win rate bar chart
    """
    from storage.database import get_all_checked_recommendations

    try:
        recs = get_all_checked_recommendations()
        if not recs:
            logger.warning("Chart: no checked recommendations")
            return None

        checked = [r for r in recs if r.get("result_pct") is not None]
        if not checked:
            return None

        # ---- Prepare data ----
        returns = [r["result_pct"] for r in checked]
        success_count = stats.get("success", 0)
        neutral_count = stats.get("neutral", 0)
        failure_count = stats.get("failure", 0)
        win_count = stats.get("win_count", 0)
        total_checked = stats.get("total_checked", 0)

        # Sort by signal_date for cumulative chart
        dated = sorted(checked, key=lambda r: r.get("signal_date", ""))
        cum_returns = []
        running = 0.0
        for r in dated:
            running += r["result_pct"]
            cum_returns.append(running)

        # Score bins
        bins_data = stats.get("score_bins", [])
        bin_labels = []
        bin_win_rates = []
        for b in sorted(bins_data, key=lambda x: x["score_bin"]):
            total_b = b["total"]
            if total_b > 0:
                # win rate uses >0% to be consistent
                pass  # calculated separately below

        # Recalculate score bins with >0% win rate from raw recs
        score_bin_map: dict[str, list[float]] = {"high (≥70)": [], "mid (60-69)": [], "low (<60)": []}
        for r in checked:
            sc = r.get("composite_score", 0) or 0
            ret = r["result_pct"]
            if sc >= 70:
                score_bin_map["high (≥70)"].append(ret)
            elif sc >= 60:
                score_bin_map["mid (60-69)"].append(ret)
            else:
                score_bin_map["low (<60)"].append(ret)

        bin_labels = []
        bin_win_rates = []
        bin_counts = []
        for label, rets in score_bin_map.items():
            if rets:
                bin_labels.append(label)
                bin_win_rates.append(round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1))
                bin_counts.append(len(rets))

        # ---- Figure ----
        fig, axes = plt.subplots(2, 2, figsize=(12, 9))
        fig.patch.set_facecolor("#0d1117")
        for ax in axes.flat:
            ax.set_facecolor("#161b22")
            ax.tick_params(colors="#c9d1d9")
            ax.xaxis.label.set_color("#c9d1d9")
            ax.yaxis.label.set_color("#c9d1d9")
            ax.title.set_color("#e6edf3")
            for spine in ax.spines.values():
                spine.set_edgecolor("#30363d")

        title_date = datetime.now().strftime("%d.%m.%Y")
        fig.suptitle(
            f"S&P 500 Bounce Bot — Статистика {title_date}",
            color="#e6edf3", fontsize=14, fontweight="bold", y=0.98,
        )

        # ---- Panel 1: Donut — Win / Neutral / Loss ----
        ax1 = axes[0, 0]
        if total_checked > 0:
            sizes = [win_count, neutral_count, failure_count]
            labels_donut = [
                f"В плюсе\n{win_count} ({stats.get('win_rate_pct', 0)}%)",
                f"Нейтрально\n{neutral_count}",
                f"В минусе\n{failure_count}",
            ]
            colors_donut = ["#238636", "#6e7681", "#da3633"]
            non_zero = [(s, l, c) for s, l, c in zip(sizes, labels_donut, colors_donut) if s > 0]
            if non_zero:
                sz, lb, cl = zip(*non_zero)
                wedges, texts, autotexts = ax1.pie(
                    sz, labels=lb, colors=cl,
                    autopct="%1.0f%%", startangle=90,
                    wedgeprops={"width": 0.6, "edgecolor": "#0d1117"},
                    textprops={"color": "#c9d1d9", "fontsize": 9},
                )
                for at in autotexts:
                    at.set_color("#e6edf3")
                    at.set_fontsize(9)
        ax1.set_title(f"Итог проверок ({total_checked} рек.)", pad=8)

        # ---- Panel 2: Returns histogram ----
        ax2 = axes[0, 1]
        if returns:
            bins_hist = np.linspace(min(returns) - 1, max(returns) + 1, 25)
            pos_returns = [r for r in returns if r > 0]
            neg_returns = [r for r in returns if r <= 0]
            if neg_returns:
                ax2.hist(neg_returns, bins=bins_hist, color="#da3633", alpha=0.8, label="Убыток")
            if pos_returns:
                ax2.hist(pos_returns, bins=bins_hist, color="#238636", alpha=0.8, label="Прибыль")
            ax2.axvline(0, color="#f0f6fc", linewidth=1, linestyle="--", alpha=0.6)
            avg_ret = stats.get("avg_result_pct") or 0
            ax2.axvline(avg_ret, color="#f0883e", linewidth=1.5, linestyle="--",
                        label=f"Среднее {avg_ret:+.2f}%")
            ax2.set_xlabel("Результат (%)")
            ax2.set_ylabel("Количество")
            ax2.legend(fontsize=8, facecolor="#21262d", labelcolor="#c9d1d9", edgecolor="#30363d")
        ax2.set_title("Распределение результатов")

        # ---- Panel 3: Cumulative P&L ----
        ax3 = axes[1, 0]
        if cum_returns:
            x = list(range(1, len(cum_returns) + 1))
            color_line = "#58a6ff"
            ax3.plot(x, cum_returns, color=color_line, linewidth=1.5)
            ax3.fill_between(
                x, cum_returns, 0,
                where=[v >= 0 for v in cum_returns], alpha=0.2, color="#238636",
            )
            ax3.fill_between(
                x, cum_returns, 0,
                where=[v < 0 for v in cum_returns], alpha=0.2, color="#da3633",
            )
            ax3.axhline(0, color="#6e7681", linewidth=0.8, linestyle="--")
            ax3.set_xlabel("Рекомендация №")
            ax3.set_ylabel("Накопленный %")
            final = cum_returns[-1]
            ax3.annotate(
                f"{final:+.1f}%",
                xy=(len(cum_returns), final),
                xytext=(-30, 10 if final >= 0 else -20),
                textcoords="offset points",
                color="#f0f6fc", fontsize=9,
                arrowprops={"arrowstyle": "->", "color": "#6e7681"},
            )
        ax3.set_title("Накопленный P&L (последовательно)")

        # ---- Panel 4: Score band → Win rate ----
        ax4 = axes[1, 1]
        if bin_labels:
            bar_colors = ["#238636" if wr >= 55 else ("#f0883e" if wr >= 45 else "#da3633")
                          for wr in bin_win_rates]
            bars = ax4.bar(bin_labels, bin_win_rates, color=bar_colors, edgecolor="#0d1117", width=0.5)
            ax4.axhline(50, color="#6e7681", linewidth=0.8, linestyle="--")
            for bar, cnt, wr in zip(bars, bin_counts, bin_win_rates):
                ax4.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 1,
                    f"{wr}%\nn={cnt}",
                    ha="center", va="bottom", color="#c9d1d9", fontsize=9,
                )
            ax4.set_ylabel("Win rate (>0%)")
            ax4.set_ylim(0, max(bin_win_rates) + 15 if bin_win_rates else 100)
            ax4.tick_params(axis="x", labelsize=8)
        ax4.set_title("Win rate по score-баллам")

        plt.tight_layout(rect=[0, 0, 1, 0.96])

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        logger.error(f"Chart generation failed: {e}", exc_info=True)
        return None
