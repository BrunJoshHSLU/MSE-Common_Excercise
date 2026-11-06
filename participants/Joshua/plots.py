"""Figures for one CE1 run, in the style of the Wollongong results slides (Oct 2026).

Called automatically by run.py. To re-plot a run that is already saved (no new simulation):

    python participants/Joshua/plots.py --climate VT --period train

Each figure compares your controller with the baseline, the tuned PI thermostat (common/tuned_pi.py,
battery idle; runs stored in common/baselines/). The reference r_k is the official one.
Figures are written to results/<climate>_<period>/figures/:
    load_vs_reference.png        district net load vs reference r_k                    (slides 27/28)
    daily_tracking.png           daily NMBE and CV-RMSE distributions                   (slide 29)
    tracking_vs_comfort.png      CV-RMSE vs median-building discomfort                   (slide 21)
    comfort_exceedance.png       % of hours outside the comfort band, per building       (slide 30)
    discomfort_per_building.png  K*h per building on a log scale                         (slide 22)
    flexibility_fairness.png     share of flexibility effort vs share of energy          (takeaway 3)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import torch  # noqa: F401  (Windows: load torch DLLs before numpy/pandas)
import matplotlib

matplotlib.use('Agg')  # write files only, never open a window
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for p in (REPO_ROOT / 'common', REPO_ROOT, HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from common.evaluation import (COMFORT_BOUNDS, PERIODS, YEAR_START, SimulationResult,  # noqa: E402
                               compute_metrics, comfort_violation, load_baseline, load_reference)

# --------------------------------------------------------------------------------------
# Style: light surface, recessive chrome, fixed colour per run.
# --------------------------------------------------------------------------------------
SURFACE = '#fcfcfb'
INK = '#0b0b0b'
INK_2 = '#52514e'
MUTED = '#898781'
GRID = '#e1e0d9'
AXIS = '#c3c2b7'
CONTROLLER = '#2a78d6'   # categorical slot 1 (blue)
BASELINE = '#898781'     # de-emphasised comparison (muted gray): the tuned PI
REFERENCE = INK          # the black reference line, as on the slides
SEQ_BLUE = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']

ID_PREFIX = 'resstock-amy2018-2021-release-1-'

plt.rcParams.update({
    'figure.facecolor': SURFACE, 'axes.facecolor': SURFACE, 'savefig.facecolor': SURFACE,
    'font.family': ['Segoe UI', 'DejaVu Sans', 'sans-serif'], 'font.size': 10,
    'text.color': INK, 'axes.labelcolor': INK_2, 'axes.titlecolor': INK,
    'axes.titlesize': 12, 'axes.titleweight': 'bold', 'axes.titlelocation': 'left', 'axes.titlepad': 10,
    'axes.edgecolor': AXIS, 'axes.linewidth': 0.8,
    'axes.spines.top': False, 'axes.spines.right': False,
    'axes.grid': True, 'grid.color': GRID, 'grid.linewidth': 0.6,
    'xtick.color': MUTED, 'ytick.color': MUTED, 'xtick.labelcolor': INK_2, 'ytick.labelcolor': INK_2,
    'legend.frameon': False, 'legend.fontsize': 9,
    'lines.linewidth': 1.4, 'lines.solid_capstyle': 'round',
})


@dataclass
class Run:
    name: str
    result: SimulationResult
    report: Dict
    color: str


def _short_id(name: str) -> str:
    return str(name).replace(ID_PREFIX, '')


def _timestamps(index, dt_h: float) -> pd.DatetimeIndex:
    return YEAR_START + pd.to_timedelta(np.asarray(index) * dt_h, unit='h')


def _label(result: SimulationResult) -> str:
    season = PERIODS[result.climate]['season']
    return f'{result.climate} ({season}), {result.period} month'


def _save(fig, path: Path):
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# --------------------------------------------------------------------------------------
# Individual figures
# --------------------------------------------------------------------------------------

def plot_load_vs_reference(runs: List[Run], path: Path):
    main = runs[0].result
    dt = main.dt_h
    r = load_reference(main.climate, main.start, len(main.load)) / dt
    t = _timestamps(main.load.index, dt)

    fig, ax = plt.subplots(figsize=(13, 4.6))
    ax.fill_between(t, 0, r, step='post', color=GRID, alpha=0.6, linewidth=0, zorder=1)
    # Draw comparisons first (thin), your controller on top.
    for z, run in enumerate(reversed(runs), start=2):
        main_run = run is runs[0]
        ax.plot(t, (run.result.district_load / dt).to_numpy(), color=run.color,
                lw=1.4 if main_run else 1.0, alpha=1.0 if main_run else 0.85, label=run.name, zorder=z)
    ax.step(t, r, where='post', color=REFERENCE, lw=2.2, label='Reference $r_k$', zorder=len(runs) + 2)

    ax.set_ylim(bottom=0)
    ax.set_xlim(t[0], t[-1])
    ax.set_ylabel('District net load [kWh h$^{-1}$]')
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    ax.set_title(f'District load vs reference: {_label(main)}')
    ax.legend(loc='upper left', bbox_to_anchor=(0, -0.12), ncol=len(runs) + 1)
    _save(fig, path)


def _violin(ax, data, positions, colors):
    parts = ax.violinplot(data, positions=positions, widths=0.7, showextrema=False)
    for body, c in zip(parts['bodies'], colors):
        body.set_facecolor(c)
        body.set_edgecolor(c)
        body.set_alpha(0.28)
    ax.boxplot(data, positions=positions, widths=0.16, showfliers=False, patch_artist=True,
               boxprops=dict(facecolor=SURFACE, edgecolor=INK_2, linewidth=0.9),
               medianprops=dict(color=INK, linewidth=1.4),
               whiskerprops=dict(color=INK_2, linewidth=0.9), capprops=dict(color=INK_2, linewidth=0.9))
    rng = np.random.default_rng(0)
    for x, d, c in zip(positions, data, colors):
        ax.scatter(x + rng.uniform(-0.12, 0.12, len(d)), d, s=14, color=c, alpha=0.75,
                   edgecolor=SURFACE, linewidth=0.5, zorder=3)


def plot_daily_tracking(runs: List[Run], path: Path):
    pos = list(range(1, len(runs) + 1))
    fig, axes = plt.subplots(2, 1, figsize=(3 + 2.2 * len(runs), 7), sharex=True)
    for ax, col, ylabel in [(axes[0], 'daily_nmbe_pct', 'Daily NMBE [%]'),
                            (axes[1], 'daily_cvrmse_pct', 'Daily CV-RMSE [%]')]:
        data = [run.report['daily'][col].dropna().to_numpy() for run in runs]
        _violin(ax, data, pos, [run.color for run in runs])
        ax.set_ylabel(ylabel)
        ax.grid(axis='x', visible=False)
        for x, d in zip(pos, data):  # median as a direct label
            ax.annotate(f'median {np.median(d):.1f}', (x + 0.38, np.median(d)), va='center',
                        fontsize=8.5, color=INK_2)
    axes[0].axhline(0, color=INK_2, lw=0.9, ls='--', zorder=0)
    axes[1].set_ylim(bottom=0)
    axes[1].set_xticks(pos, [run.name for run in runs])
    axes[1].set_xlim(0.4, len(runs) + 0.9)
    axes[0].set_title(f'Daily tracking errors: {_label(runs[0].result)}')
    _save(fig, path)


def plot_tracking_vs_comfort(runs: List[Run], path: Path):
    fig, ax = plt.subplots(figsize=(7, 5.2))
    xs = [run.report['summary']['cvrmse_pct'] for run in runs]
    ys = [run.report['summary']['discomfort_median_Kh'] for run in runs]
    x_span, y_span = max(xs) * 1.35 + 5, max(ys) * 1.3 + 5
    for i, (run, x, y) in enumerate(zip(runs, xs, ys)):
        # Points that (almost) coincide with an earlier one get their label stacked below it.
        stacked = sum(abs(x - xs[j]) < 0.04 * x_span and abs(y - ys[j]) < 0.06 * y_span for j in range(i))
        ax.scatter([x], [y], s=110 if not stacked else 34, color=run.color, edgecolor=SURFACE,
                   linewidth=1.5 if not stacked else 0.8, zorder=3 + i, label=run.name)
        ax.annotate(f'{run.name}\n{x:.1f} %, {y:.0f} K·h', (x, y), xytext=(10, 6 - 26 * stacked),
                    textcoords='offset points', fontsize=8.5, color=INK_2)
    ax.set_xlim(0, x_span)
    ax.set_ylim(0, y_span)
    ax.set_xlabel('CV-RMSE [%]  (tracking error; lower left is better)')
    ax.set_ylabel('Cumulative discomfort, median building [K·h]')
    ax.set_title(f'Tracking vs comfort: {_label(runs[0].result)}')
    ax.legend(loc='upper left')
    _save(fig, path)


def _exceedance_pct(res: SimulationResult) -> pd.Series:
    t_min, t_max = COMFORT_BOUNDS[PERIODS[res.climate]['season']]
    v = comfort_violation(res.temperature, t_min, t_max)
    return 100.0 * (v > 0).mean()


def plot_comfort_exceedance(runs: List[Run], path: Path):
    df = pd.DataFrame({run.name: _exceedance_pct(run.result) for run in runs}).T
    df = df[df.mean().sort_values().index]  # buildings ordered by mean comfort burden

    cmap = LinearSegmentedColormap.from_list('seq_blue', ['#f3f8fe'] + SEQ_BLUE)
    vmax = max(10.0, float(np.ceil(df.to_numpy().max() / 10) * 10))
    fig, ax = plt.subplots(figsize=(13, 1.2 + 0.55 * len(df)))
    im = ax.imshow(df.to_numpy(), aspect='auto', cmap=cmap, vmin=0, vmax=vmax)
    for i in range(df.shape[0]):
        for j in range(df.shape[1]):
            val = df.iat[i, j]
            ax.text(j, i, f'{val:.0f}', ha='center', va='center', fontsize=7,
                    color='#ffffff' if val > 0.55 * vmax else INK)
    ax.set_yticks(range(len(df)), df.index)
    ax.set_xticks(range(df.shape[1]), [_short_id(c) for c in df.columns], rotation=90, fontsize=7)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xlabel('Building (ResStock ID), ordered by mean comfort burden')
    cb = fig.colorbar(im, ax=ax, pad=0.01, fraction=0.03)
    cb.set_label('Comfort exceedance\n[% of hours]', color=INK_2, fontsize=8.5)
    cb.outline.set_visible(False)
    main = runs[0].result
    t_min, t_max = COMFORT_BOUNDS[PERIODS[main.climate]['season']]
    ax.set_title(f'Hours outside {t_min:g}-{t_max:g} °C per building: {_label(main)}')
    _save(fig, path)


def plot_discomfort_per_building(runs: List[Run], path: Path):
    floor = 0.1  # log axis: buildings with (almost) no discomfort are drawn at 0.1 K*h
    fig, ax = plt.subplots(figsize=(9, 1.6 + 0.9 * len(runs)))
    rng = np.random.default_rng(1)
    for i, run in enumerate(runs):
        k = run.report['buildings']['discomfort_Kh']
        y = len(runs) - 1 - i
        vals = np.clip(k.to_numpy(), floor, None)
        ax.scatter(vals, y + rng.uniform(-0.18, 0.18, len(vals)), s=36, color=run.color, alpha=0.8,
                   edgecolor=SURFACE, linewidth=0.8, zorder=3)
        med = max(float(np.median(k)), floor)
        ax.plot([med, med], [y - 0.3, y + 0.3], color=INK, lw=2.2, zorder=4)
        ax.annotate(f'median {np.median(k):.0f} K·h', (med, y + 0.3), xytext=(4, 2),
                    textcoords='offset points', fontsize=8.5, color=INK_2)
    ax.set_xscale('log')
    lowest = min(max(float(run.report['buildings']['discomfort_Kh'].min()), floor) for run in runs)
    ax.set_xlim(10 ** np.floor(np.log10(lowest)) * 0.8, None)  # start one decade below the data
    ax.set_yticks(range(len(runs)), [run.name for run in reversed(runs)])
    ax.set_ylim(-0.6, len(runs) - 0.4)
    ax.grid(axis='y', visible=False)
    ax.set_xlabel('Cumulative discomfort per building [K·h, log scale; 0 drawn at 0.1]')
    ax.set_title(f'Discomfort per building: {_label(runs[0].result)}')
    _save(fig, path)


def plot_flexibility_fairness(run: Run, path: Path):
    b = run.report['buildings']
    x, y = b['energy_share_pct'], b['flex_effort_share_pct']

    fig, ax = plt.subplots(figsize=(6.5, 6))
    lim = float(max(x.max(), y.max()) * 1.12)
    ax.plot([0, lim], [0, lim], color=MUTED, lw=1.0, ls='--', zorder=1)
    ax.text(lim * 0.97, lim * 0.93, 'effort = energy share', ha='right', fontsize=8, color=MUTED, rotation=45,
            rotation_mode='anchor', transform_rotates_text=True)
    ax.scatter(x, y, s=48, color=run.color, edgecolor=SURFACE, linewidth=1.0, zorder=3)
    # Label the three most over-burdened buildings; stagger the labels so neighbours don't collide.
    top = (y / x).sort_values(ascending=False).index[:3]
    placements = [((-6, 4), 'right'), ((6, -12), 'left'), ((6, 6), 'left')]
    for name, (offset, ha) in zip(sorted(top, key=lambda n: x[n]), placements):
        ax.annotate(_short_id(name), (x[name], y[name]), xytext=offset, textcoords='offset points',
                    ha=ha, fontsize=8, color=INK_2)
    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)
    ax.set_aspect('equal')
    ax.set_xlabel('Share of baseline energy [%]')
    ax.set_ylabel('Share of flexibility effort [%]\n(|load - baseline load|, summed)')
    ax.set_title(f'Who carries the flexibility: {_label(run.result)}')
    ax.text(0.03, 0.97, 'above the line: more effort than its energy share', transform=ax.transAxes,
            va='top', fontsize=8, color=MUTED)
    _save(fig, path)


# --------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------

def plot_all(result: SimulationResult, report: Dict, baseline: Optional[SimulationResult], out_dir) -> Path:
    """Write all figures for one run to <out_dir>/figures and return that folder.

    `report` is compute_metrics(result, baseline); `baseline` (the tuned PI run) is optional.
    """
    fig_dir = Path(out_dir) / 'figures'
    fig_dir.mkdir(parents=True, exist_ok=True)

    runs = [Run('Your controller', result, report, CONTROLLER)]
    if baseline is not None:
        runs.append(Run('Baseline (tuned PI)', baseline, compute_metrics(baseline), BASELINE))

    plot_load_vs_reference(runs, fig_dir / 'load_vs_reference.png')
    plot_daily_tracking(runs, fig_dir / 'daily_tracking.png')
    plot_tracking_vs_comfort(runs, fig_dir / 'tracking_vs_comfort.png')
    plot_comfort_exceedance(runs, fig_dir / 'comfort_exceedance.png')
    plot_discomfort_per_building(runs, fig_dir / 'discomfort_per_building.png')

    # Effort is measured against the baseline load. While your controller still equals the
    # baseline (tuned PI), effort is only CSV rounding noise (~1e-5 kWh), shares are meaningless:
    # skip that figure until the total effort exceeds 1 ppm of the baseline energy.
    fairness_path = fig_dir / 'flexibility_fairness.png'
    b = report['buildings']
    if baseline is not None and b['flex_effort_kwh'].sum() > 1e-6 * b['baseline_energy_kwh'].sum():
        plot_flexibility_fairness(runs[0], fairness_path)
    else:
        fairness_path.unlink(missing_ok=True)
        if baseline is not None:
            print('Skipped flexibility_fairness.png: your controller does not deviate from the baseline yet.')
    return fig_dir


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Plot a saved run (results/<climate>_<period>/timeseries.csv).')
    parser.add_argument('--climate', choices=['TX', 'VT'], default='VT')
    parser.add_argument('--period', choices=['train', 'test'], default='train')
    parser.add_argument('--no-baseline', action='store_true', help='leave out the baseline (tuned PI)')
    args = parser.parse_args()

    out_dir = HERE / 'results' / f'{args.climate.lower()}_{args.period}'
    result = SimulationResult.from_csv(out_dir / 'timeseries.csv')
    baseline = None if args.no_baseline else load_baseline(args.climate, args.period)
    report = compute_metrics(result, baseline)
    fig_dir = plot_all(result, report, baseline, out_dir)
    print(f'Saved figures to {fig_dir.relative_to(REPO_ROOT)}')


if __name__ == '__main__':
    main()
