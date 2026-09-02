"""Render the three figures in ANALYSIS.md, straight from the marts.

Every number in the analysis comes from a query in this file against the built
warehouse -- nothing is typed in by hand, so regenerating after a model change
either reproduces the figures or visibly disagrees with the prose.

Three charts, deliberately. The brief says three and means it: a fourth would be
a fourth thing to keep true.

Design follows the project's data-viz conventions:
  * one hue (blue), stepped as an ordinal ramp -- these are ordered quantities,
    not competing identities, so a categorical palette would be wrong;
  * the ramp is validated, not eyeballed (monotone lightness, >= 0.06 L gaps,
    light end clearing 2:1 on the surface);
  * every mark carries a visible direct label, which is also the required relief
    for the lightest step sitting under 3:1 against the surface;
  * recessive grid and axes, no chartjunk, no second y-axis anywhere.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "marketplace.duckdb"
OUT = REPO / "docs" / "img"

# --- palette -----------------------------------------------------------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

# Ordinal blue ramp: steps 250 / 350 / 450 / 550 / 650. Monotone in lightness,
# single hue, light end at 2.06:1 against the surface.
RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]
ACCENT = "#2a78d6"
CONTEXT = "#d8d7d0"  # de-emphasis gray for the cohort context lines

plt.rcParams.update({
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "text.color": INK,
    "axes.labelcolor": INK_2,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.edgecolor": BASELINE,
    "axes.linewidth": 0.8,
    "xtick.major.size": 0,
    "ytick.major.size": 0,
})


def style(ax, *, xgrid=False, ygrid=False):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(BASELINE)
    ax.spines["bottom"].set_color(BASELINE)
    if xgrid:
        ax.xaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    if ygrid:
        ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)


def rounded_hbar(ax, y, width, height, color, *, radius=0.055, zorder=3):
    """A horizontal bar with a rounded data end and a square baseline end."""
    r = min(radius, height / 2, max(width, 1e-9) / 2)
    ax.add_patch(FancyBboxPatch(
        (0 + r, y - height / 2 + r), max(width - 2 * r, 1e-6), height - 2 * r,
        boxstyle=f"round,pad={r}", linewidth=0, facecolor=color, zorder=zorder,
    ))
    # Square off the baseline end so the bar is anchored, not floating.
    ax.add_patch(Rectangle(
        (0, y - height / 2), min(r * 1.2, width), height,
        linewidth=0, facecolor=color, zorder=zorder,
    ))


def title_block(fig, title, subtitle):
    fig.text(0.012, 0.965, title, fontsize=13.5, fontweight="bold", color=INK, va="top")
    fig.text(0.012, 0.895, subtitle, fontsize=9.5, color=INK_2, va="top")


# =============================================================================
def chart_funnel(con) -> None:
    rows = con.execute("""
        select
            step_number,
            step_name,
            count(*) filter (where is_reached_in_sequence) as reached
        from main_marts.fct_funnel_steps
        group by step_number, step_name
        order by step_number
    """).fetchall()

    labels = [r[1].capitalize() for r in rows]
    counts = [r[2] for r in rows]
    base = counts[0]

    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    ys = list(range(len(rows)))[::-1]

    for i, (y, n) in enumerate(zip(ys, counts)):
        rounded_hbar(ax, y, n, 0.56, RAMP[i])
        pct = 100 * n / base
        ax.text(n + base * 0.012, y, f"{n:,}", va="center", ha="left",
                fontsize=10.5, fontweight="bold", color=INK)
        ax.text(n + base * 0.012, y - 0.30, f"{pct:.1f}% of orders placed",
                va="center", ha="left", fontsize=8.5, color=MUTED)
        if i > 0:
            lost = counts[i - 1] - n
            step_conv = 100 * n / counts[i - 1]
            # Sits in the clean gap between two bars, so no halo is needed.
            ax.text(base * 0.014, y + 0.5,
                    f"↓ {lost:,} lost   ·   {step_conv:.1f}% of previous step",
                    va="center", ha="left", fontsize=8.5, color=INK_2)

    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=11, color=INK)
    ax.set_xlim(0, base * 1.30)
    ax.set_xticks([])
    ax.set_ylim(-0.7, len(rows) - 0.35)
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(BASELINE)
    ax.set_axisbelow(True)

    title_block(
        fig,
        "Order lifecycle funnel",
        "Orders reaching each step, counted only where every prior step was reached.\n"
        "No single step dominates: four transitions each shed roughly 1% of orders.",
    )
    fig.subplots_adjust(left=0.10, right=0.985, top=0.775, bottom=0.05)
    fig.savefig(OUT / "funnel_drop_off.png", dpi=200)
    plt.close(fig)
    print("  wrote docs/img/funnel_drop_off.png")


# =============================================================================
def chart_retention(con) -> None:
    max_month = 12

    cohorts = con.execute(f"""
        select cohort_month, months_since_first_order, retention_rate, cohort_size
        from main_marts.agg_cohort_retention
        where is_fully_observed
          and months_since_first_order between 1 and {max_month}
          and cohort_size >= 500
        order by cohort_month, months_since_first_order
    """).fetchall()

    pooled = con.execute(f"""
        select
            months_since_first_order,
            sum(active_buyers) * 1.0 / sum(cohort_size) as retention_rate,
            sum(cohort_size) as buyers
        from main_marts.agg_cohort_retention
        where is_fully_observed
          and months_since_first_order between 1 and {max_month}
        group by months_since_first_order
        order by months_since_first_order
    """).fetchall()

    series: dict = {}
    for cm, m, rate, _size in cohorts:
        series.setdefault(cm, ([], []))
        series[cm][0].append(m)
        series[cm][1].append(100 * rate)

    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    style(ax, ygrid=True)

    for xs, ys in series.values():
        ax.plot(xs, ys, color=CONTEXT, linewidth=0.9, alpha=0.85, zorder=2,
                solid_capstyle="round")

    px = [r[0] for r in pooled]
    py = [100 * r[1] for r in pooled]
    ax.plot(px, py, color=ACCENT, linewidth=2.0, zorder=4, solid_capstyle="round")
    ax.plot(px, py, "o", color=ACCENT, markersize=5.0, zorder=5,
            markeredgecolor=SURFACE, markeredgewidth=1.4)

    # Direct labels on the two ends of the pooled curve only -- never a number
    # on every point.
    ax.annotate(f"{py[0]:.2f}%", (px[0], py[0]), textcoords="offset points",
                xytext=(9, 9), ha="left", fontsize=9.5, fontweight="bold", color=INK)
    ax.annotate(f"{py[-1]:.2f}%", (px[-1], py[-1]), textcoords="offset points",
                xytext=(-9, 9), ha="right", fontsize=9.5, fontweight="bold", color=INK)

    ax.set_xlim(0.6, max_month + 0.6)
    ax.set_xticks(range(1, max_month + 1))
    ax.set_ylim(0, max(max(py) * 1.9, 1.0))
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.1f}%")
    ax.set_xlabel("Months since first order", color=INK_2, fontsize=9.5)

    ax.legend(
        handles=[
            Line2D([], [], color=ACCENT, linewidth=2.0, label="All cohorts pooled"),
            Line2D([], [], color=CONTEXT, linewidth=1.6,
                   label="Individual monthly cohorts (\u2265 500 buyers)"),
        ],
        loc="upper right", frameon=False, fontsize=9, labelcolor=INK_2,
    )

    title_block(
        fig,
        "Repeat-purchase rate by acquisition cohort",
        "Share of a cohort placing another order in each later month. Month 0 is 100% by\n"
        "construction and is omitted; only fully-observed months are plotted.",
    )
    fig.subplots_adjust(left=0.075, right=0.985, top=0.775, bottom=0.125)
    fig.savefig(OUT / "cohort_retention.png", dpi=200)
    plt.close(fig)
    print("  wrote docs/img/cohort_retention.png")


# =============================================================================
def chart_delivery(con) -> None:
    rows = con.execute("""
        select
            buyer_region,
            count(*)                                          as orders,
            quantile_cont(days_to_delivery, 0.5)              as median_days,
            quantile_cont(days_to_delivery, 0.9)              as p90_days,
            avg(case when is_late_delivery then 1.0 else 0 end) as late_rate
        from main_marts.fct_orders
        where reached_delivered and days_to_delivery is not null
        group by buyer_region
        order by median_days
    """).fetchall()

    regions = [r[0] for r in rows]
    medians = [r[2] for r in rows]
    p90s = [r[3] for r in rows]
    late = [r[4] for r in rows]

    fig, ax = plt.subplots(figsize=(9.6, 4.8))
    style(ax, xgrid=True)

    ys = list(range(len(rows)))[::-1]
    med_color, p90_color = RAMP[0], RAMP[3]

    for y, m, p in zip(ys, medians, p90s):
        ax.plot([m, p], [y, y], color=med_color, linewidth=2.0,
                solid_capstyle="round", zorder=3)
        ax.plot([m], [y], "o", markersize=9, color=med_color, zorder=4,
                markeredgecolor=SURFACE, markeredgewidth=1.6)
        ax.plot([p], [y], "o", markersize=9, color=p90_color, zorder=4,
                markeredgecolor=SURFACE, markeredgewidth=1.6)
        ax.text(m, y + 0.30, f"{m:.0f}d", ha="center", va="bottom",
                fontsize=9, fontweight="bold", color=INK)
        ax.text(p, y + 0.30, f"{p:.0f}d", ha="center", va="bottom",
                fontsize=9, fontweight="bold", color=INK)

    ax.set_yticks(ys)
    ax.set_yticklabels(
        [f"{r}\n{n:,} orders · {l:.1%} late" for r, n, l in
         zip(regions, [r[1] for r in rows], late)],
        fontsize=9.5, color=INK,
    )
    for tick in ax.get_yticklabels():
        tick.set_linespacing(1.5)
    ax.set_xlim(0, max(p90s) * 1.12)
    ax.set_ylim(-0.65, len(rows) - 0.3)
    ax.set_xlabel("Days from purchase to delivery", color=INK_2, fontsize=9.5)

    ax.legend(
        handles=[
            Line2D([], [], marker="o", linestyle="none", markersize=8,
                   color=med_color, label="Median"),
            Line2D([], [], marker="o", linestyle="none", markersize=8,
                   color=p90_color, label="90th percentile"),
        ],
        loc="lower left", bbox_to_anchor=(0.0, 1.005), ncol=2,
        frameon=False, fontsize=9, labelcolor=INK_2, handletextpad=0.4,
        columnspacing=1.8,
    )

    title_block(
        fig,
        "Delivery time by buyer region",
        "Median and 90th-percentile days from purchase to delivery, for delivered orders.\n"
        "The tail, not the median, is what separates the regions.",
    )
    fig.subplots_adjust(left=0.215, right=0.985, top=0.745, bottom=0.125)
    fig.savefig(OUT / "delivery_time_by_region.png", dpi=200)
    plt.close(fig)
    print("  wrote docs/img/delivery_time_by_region.png")


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"No warehouse at {DB}. Run `make build` first.")
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB), read_only=True)
    try:
        print("Rendering figures ...")
        chart_funnel(con)
        chart_retention(con)
        chart_delivery(con)
    finally:
        con.close()
    print("Done.")


if __name__ == "__main__":
    main()
