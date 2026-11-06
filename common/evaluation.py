"""Shared evaluation for IEA EBC Annex 96 Common Exercise 1.

Everyone in the group uses this module, so all controllers are scored the same way.
The formulas follow the CE1 benchmark formulation (Wollongong slides, Oct 2026) and
common/README.md:

    Reference        r_k  = official CE1 target (district_target.csv): daily mean of the official
                            baseline portfolio load. Our baseline runs (common/baselines/) use the
                            tuned PI thermostat instead (common/tuned_pi.py), r_k stays official.
    District load    y_k  = sum_i y_k^i, with y_k^i = max(0, net load of building i)  (no PV export)
    NMBE     [%]     = 100 / mean(r) * mean(y - r)
    CV-RMSE  [%]     = 100 / mean(r) * sqrt(mean((y - r)^2))
    Discomfort       v_k^i = max(0, T_k^i - T_max) + max(0, T_min - T_k^i)          [K]
                     K_i   = sum_k v_k^i * dt                                       [K h]
                     K_bar = mean_i K_i      (the slides plot the median building as well)
    Comfort bounds   heating season (VT) 20-24 degC, cooling season (TX) 22-26 degC

Secondary metrics (common/README.md): energy, peak demand (+ time), daily peak-to-valley
ratio, daily load factor, daily ramping. Cost and emissions are NOT reported: the CE1
datasets contain no price or carbon-intensity data (CityLearn fills them with zeros).

Typical use (see participants/_template/run.py):

    from evaluation import make_env, collect, evaluate, load_baseline
    env = make_env('VT', period='test')
    ... run your controller until env.terminated ...
    result = collect(env, 'VT', 'test')
    report = evaluate(result, load_baseline('VT', 'test'))  # prints a summary, returns tables
    result.to_csv('results/vt_test.csv')                    # lets others re-evaluate your run

A saved run can be re-scored from the command line:

    python common/evaluation.py participants/<name>/results/vt_test.csv
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

COMMON_DIR = Path(__file__).resolve().parent
DATASETS_DIR = COMMON_DIR / 'data' / 'datasets'

# Hour-of-year index ranges (inclusive) of the CE1 training and test months.
# The datasets are ResStock AMY 2018, so time step 0 is 2018-01-01 00:00.
PERIODS = {
    'VT': {'season': 'heating', 'train': (0, 743), 'test': (744, 1415)},       # January / February
    'TX': {'season': 'cooling', 'train': (3624, 4343), 'test': (4344, 5087)},  # June / July
}
COMFORT_BOUNDS = {'heating': (20.0, 24.0), 'cooling': (22.0, 26.0)}  # (T_min, T_max) in degC
YEAR_START = pd.Timestamp('2018-01-01 00:00')


# --------------------------------------------------------------------------------------
# Environment helpers
# --------------------------------------------------------------------------------------

def dataset_dir(climate: str) -> Path:
    return DATASETS_DIR / f'annex96_ce1_{climate.lower()}_neighborhood'


def make_env(climate: str, period: str = 'train', central_agent: bool = True, **kwargs):
    """Create the CE1 CityLearn environment for a climate ('VT'/'TX') and period ('train'/'test')."""
    from citylearn.citylearn import CityLearnEnv

    climate = climate.upper()
    start, end = PERIODS[climate][period]
    # One episode = the whole period (the VT schema hard-codes 744 steps, which breaks February).
    kwargs.setdefault('episode_time_steps', end - start + 1)
    return CityLearnEnv(
        schema=str(dataset_dir(climate) / 'schema.json'),
        central_agent=central_agent,
        simulation_start_time_step=start,
        simulation_end_time_step=end,
        **kwargs,
    )


def load_reference(climate: str, start: int, n: int) -> np.ndarray:
    """Official reference r_k for `n` time steps starting at hour-of-year `start`."""
    ref = pd.read_csv(dataset_dir(climate) / 'district_target.csv')['district_load_target'].to_numpy()
    return ref[start:start + n]


# --------------------------------------------------------------------------------------
# Simulation result container
# --------------------------------------------------------------------------------------

@dataclass
class SimulationResult:
    """Building-level time series of one simulation run.

    load:        DataFrame (time steps x buildings), grid import per building in kWh per step
                 (negative net load, i.e. PV surplus, is already clipped to 0: no export)
    temperature: DataFrame (time steps x buildings), indoor dry-bulb temperature in degC
    The index of both is the absolute hour-of-year time step.
    """
    climate: str
    period: str
    load: pd.DataFrame
    temperature: pd.DataFrame
    dt_h: float = 1.0

    @property
    def start(self) -> int:
        return int(self.load.index[0])

    @property
    def district_load(self) -> pd.Series:
        return self.load.sum(axis=1)

    def to_csv(self, path) -> Path:
        """Save in long format (one row per time step and building)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        long = pd.DataFrame({
            'time_step': np.repeat(self.load.index.to_numpy(), self.load.shape[1]),
            'building': np.tile(self.load.columns.to_numpy(), self.load.shape[0]),
            'load_kwh': self.load.to_numpy().ravel(),
            'indoor_temp_c': self.temperature.to_numpy().ravel(),
        })
        long.insert(0, 'period', self.period)
        long.insert(0, 'climate', self.climate)
        long.to_csv(path, index=False)
        return path

    @classmethod
    def from_csv(cls, path, dt_h: float = 1.0) -> 'SimulationResult':
        long = pd.read_csv(path)
        load = long.pivot(index='time_step', columns='building', values='load_kwh')
        temp = long.pivot(index='time_step', columns='building', values='indoor_temp_c')
        return cls(str(long['climate'].iloc[0]), str(long['period'].iloc[0]), load, temp, dt_h)


def collect(env, climate: str, period: str, without_storage: bool = False) -> SimulationResult:
    """Extract the building-level results from an environment after the episode has finished.

    without_storage=True takes the load the building would have had without its battery
    (used for the baseline: thermostat control, PV only).
    """
    start = env.episode_tracker.episode_start_time_step
    attr = 'net_electricity_consumption_without_storage' if without_storage else 'net_electricity_consumption'
    names = [b.name for b in env.buildings]
    load = np.column_stack([np.clip(getattr(b, attr), 0.0, None) for b in env.buildings])
    temp = np.column_stack([b.indoor_dry_bulb_temperature for b in env.buildings])
    index = pd.RangeIndex(start, start + load.shape[0], name='time_step')
    return SimulationResult(
        climate=climate.upper(),
        period=period,
        load=pd.DataFrame(load, index=index, columns=names),
        temperature=pd.DataFrame(temp, index=index, columns=names),
        dt_h=env.seconds_per_time_step / 3600.0,
    )


def run_baseline(climate: str, period: str = 'train') -> SimulationResult:
    """Simulate the baseline: tuned PI thermostat (common/tuned_pi.py), PV, battery idle.

    This replaces the official PITemperatureController, which oscillates on the CE1 building
    models (see common/tuned_pi.py). The reference r_k (district_target.csv) is unchanged.
    """
    try:
        from common.tuned_pi import TunedPIController
    except ImportError:  # evaluation.py run as a script from common/
        from tuned_pi import TunedPIController

    env = make_env(climate, period, central_agent=True)
    agent = TunedPIController(env)
    observations, _ = env.reset()
    while not env.terminated:
        observations, _, _, _, _ = env.step(agent.predict(observations))
    return collect(env, climate, period)


BASELINES_DIR = COMMON_DIR / 'baselines'


def load_baseline(climate: str, period: str = 'train') -> SimulationResult:
    """Baseline run (tuned PI) from common/baselines/ (simulated and saved there on first use)."""
    path = BASELINES_DIR / f'baseline_{climate.lower()}_{period}.csv'
    if path.exists():
        return SimulationResult.from_csv(path)
    print(f'Simulating baseline for {climate.upper()} {period} (only needed once) ...')
    baseline = run_baseline(climate, period)
    baseline.to_csv(path)
    return baseline


# --------------------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------------------

def tracking_errors(y: np.ndarray, r: np.ndarray) -> Dict[str, float]:
    """NMBE and CV-RMSE in %, normalised by the mean reference."""
    y, r = np.asarray(y, float), np.asarray(r, float)
    r_mean = r.mean()
    return {
        'nmbe_pct': 100.0 * np.mean(y - r) / r_mean,
        'cvrmse_pct': 100.0 * np.sqrt(np.mean((y - r) ** 2)) / r_mean,
    }


def comfort_violation(temperature: pd.DataFrame, t_min: float, t_max: float) -> pd.DataFrame:
    """v_k^i in K per time step and building."""
    return (temperature - t_max).clip(lower=0) + (t_min - temperature).clip(lower=0)


def _daily_load_metrics(y_kw: pd.Series, steps_per_day: int) -> pd.DataFrame:
    day = (np.arange(len(y_kw)) // steps_per_day)
    g = y_kw.groupby(day)
    peak, valley, mean = g.max(), g.min(), g.mean()
    ramp = y_kw.diff().abs().groupby(day).sum(min_count=1)  # |y_i - y_{i-1}| summed per day
    return pd.DataFrame({
        'peak_kw': peak,
        'valley_kw': valley,
        'peak_to_valley': (peak / valley).where(valley > 0),  # undefined when the valley is 0
        'load_factor': mean / peak,
        'ramping_kw': ramp,
    })


def compute_metrics(result: SimulationResult, baseline: Optional[SimulationResult] = None) -> Dict[str, object]:
    """Compute all CE1 metrics.

    Returns a dict with
      'summary':   dict of portfolio-level scalar metrics
      'daily':     DataFrame with one row per day (tracking, peak, PVR, load factor, ramping)
      'buildings': DataFrame with one row per building (energy, discomfort, exceedance, ...)
    """
    season = PERIODS[result.climate]['season']
    t_min, t_max = COMFORT_BOUNDS[season]
    dt = result.dt_h
    steps_per_day = int(round(24 / dt))
    n_steps, n_buildings = result.load.shape

    y = result.district_load
    r = pd.Series(load_reference(result.climate, result.start, n_steps), index=y.index)
    if len(r) != n_steps:
        raise ValueError('Reference profile is shorter than the simulated period.')

    # --- tracking ---------------------------------------------------------------------
    summary = {'climate': result.climate, 'period': result.period, 'season': season,
               'time_steps': n_steps, 'buildings': n_buildings}
    summary.update(tracking_errors(y, r))

    day = np.arange(n_steps) // steps_per_day
    daily = pd.DataFrame([tracking_errors(y[day == d], r[day == d]) for d in np.unique(day)])
    daily.columns = ['daily_nmbe_pct', 'daily_cvrmse_pct']
    daily.insert(0, 'date', [(YEAR_START + pd.Timedelta(hours=result.start * dt + d * 24)).date()
                             for d in np.unique(day)])
    daily = pd.concat([daily, _daily_load_metrics(y / dt, steps_per_day).reset_index(drop=True)], axis=1)

    # --- comfort ----------------------------------------------------------------------
    v = comfort_violation(result.temperature, t_min, t_max)
    K_i = v.sum() * dt                         # K h per building
    exceed_h = (v > 0).sum() * dt              # hours outside the band per building
    summary.update({
        'comfort_band_c': f'{t_min:g}-{t_max:g}',
        'discomfort_mean_Kh': K_i.mean(),      # K_bar from the slides
        'discomfort_median_Kh': K_i.median(),  # "median building" on the Pareto plot
        'exceedance_hours_total': exceed_h.sum(),
        'exceedance_pct_portfolio': 100.0 * exceed_h.sum() / (n_buildings * n_steps * dt),
    })

    # --- secondary --------------------------------------------------------------------
    y_kw = y / dt
    peak_step = int(y_kw.idxmax())
    summary.update({
        'energy_kwh': y.sum(),
        'peak_kw': y_kw.max(),
        'peak_time': str(YEAR_START + pd.Timedelta(hours=peak_step * dt)),
        'daily_nmbe_median_pct': daily['daily_nmbe_pct'].median(),
        'daily_cvrmse_median_pct': daily['daily_cvrmse_pct'].median(),
        'peak_to_valley_median': daily['peak_to_valley'].median(),  # median: near-zero valleys (PV) explode the mean
        'load_factor_mean': daily['load_factor'].mean(),
        'ramping_mean_kw': daily['ramping_kw'].mean(),
    })

    buildings = pd.DataFrame({
        'energy_kwh': result.load.sum(),
        'peak_kw': (result.load / dt).max(),
        'discomfort_Kh': K_i,
        'exceedance_h': exceed_h,
        'exceedance_pct': 100.0 * exceed_h / (n_steps * dt),
    })
    buildings.index.name = 'building'

    # --- comparison with baseline -------------------------------------------------------
    if baseline is not None:
        if not baseline.load.index.equals(result.load.index):
            raise ValueError('Baseline and result cover different time steps.')
        yb = baseline.district_load
        yb_kw = yb / dt
        base_daily = _daily_load_metrics(yb_kw, steps_per_day)
        summary.update({
            'baseline_energy_kwh': yb.sum(),
            'energy_change_pct': 100.0 * (y.sum() - yb.sum()) / yb.sum(),
            'baseline_peak_kw': yb_kw.max(),
            'baseline_peak_time': str(YEAR_START + pd.Timedelta(hours=int(yb_kw.idxmax()) * dt)),
            'baseline_peak_to_valley_median': base_daily['peak_to_valley'].median(),
            'baseline_load_factor_mean': base_daily['load_factor'].mean(),
            'baseline_ramping_mean_kw': base_daily['ramping_kw'].mean(),
        })
        for col in ['peak_kw', 'peak_to_valley', 'load_factor', 'ramping_kw']:
            daily[f'baseline_{col}'] = base_daily[col].to_numpy()

        # Flexibility effort per building = how far its load moved away from its baseline.
        # Comparing the share of effort with the share of energy indicates fairness
        # (README: "whether this is proportional on a portfolio level").
        effort = (result.load - baseline.load).abs().sum()
        buildings['baseline_energy_kwh'] = baseline.load.sum()
        buildings['flex_effort_kwh'] = effort
        buildings['flex_effort_share_pct'] = 100.0 * effort / effort.sum()
        buildings['energy_share_pct'] = 100.0 * buildings['baseline_energy_kwh'] / buildings['baseline_energy_kwh'].sum()

    return {'summary': summary, 'daily': daily, 'buildings': buildings}


def print_summary(summary: Dict[str, object]) -> None:
    width = max(len(k) for k in summary)
    print('-' * (width + 20))
    for k, v in summary.items():
        print(f'{k:<{width}}  {v:>12.2f}' if isinstance(v, (float, np.floating)) else f'{k:<{width}}  {v!s:>12}')
    print('-' * (width + 20))


def evaluate(result: SimulationResult, baseline: Optional[SimulationResult] = None,
             verbose: bool = True) -> Dict[str, object]:
    """Compute and (optionally) print all metrics for a finished run."""
    report = compute_metrics(result, baseline)
    if verbose:
        print_summary(report['summary'])
    return report


# --------------------------------------------------------------------------------------
# Command line: re-score a saved run
# --------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description='Evaluate a saved CE1 run (CSV written by SimulationResult.to_csv).')
    parser.add_argument('results_csv')
    parser.add_argument('--baseline-csv', help='saved baseline run; omit to skip baseline comparison')
    parser.add_argument('--out', help='folder to write summary/daily/buildings CSVs to')
    args = parser.parse_args(argv)

    result = SimulationResult.from_csv(args.results_csv)
    baseline = SimulationResult.from_csv(args.baseline_csv) if args.baseline_csv else None
    report = evaluate(result, baseline)

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        pd.Series(report['summary']).to_csv(out / 'summary.csv', header=['value'])
        report['daily'].to_csv(out / 'daily.csv', index=False)
        report['buildings'].to_csv(out / 'buildings.csv')
        print(f'Wrote tables to {out}')


if __name__ == '__main__':
    sys.path.insert(0, str(COMMON_DIR))
    main()
