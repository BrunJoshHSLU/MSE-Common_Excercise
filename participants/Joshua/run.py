"""Run your controller on a CE1 portfolio and evaluate it with the shared metrics.

Usage (from anywhere in the repo):
    python participants/<your_folder>/run.py --climate VT --period train
    python participants/<your_folder>/run.py --climate TX --period test

Training months: VT January, TX June. Test months: VT February, TX July.
Metric definitions: common/evaluation.py and common/README.md.
"""

import argparse
import sys
from pathlib import Path

# On some Windows setups torch must be imported before pandas/numpy, otherwise
# loading its DLLs fails (WinError 1114). Harmless everywhere else.
import torch  # noqa: F401

# Make the local CityLearn copy and the shared evaluation in common/ importable
# (do NOT pip install citylearn).
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / 'common'))   # keep: lets the local citylearn package be imported
sys.path.insert(0, str(REPO_ROOT))              # add: makes `common.evaluation` importable
sys.path.insert(0, str(HERE))


import pandas as pd

from common.evaluation import collect, evaluate, load_baseline, make_env

from controller import ControllerTest
from plots import plot_all


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--climate', choices=['TX', 'VT'], default='VT')
    parser.add_argument('--period', choices=['train', 'test'], default='train')
    parser.add_argument('--no-baseline', action='store_true',
                        help='skip the comparison with the baseline (tuned PI, common/tuned_pi.py)')
    args = parser.parse_args()

    env = make_env(args.climate, args.period, central_agent=True)
    agent = ControllerTest(env)

    observations, _ = env.reset()
    while not env.terminated:
        actions = agent.predict(observations)
        observations, _, _, _, _ = env.step(actions)

    result = collect(env, args.climate, args.period)
    # Baseline = tuned PI thermostat (common/tuned_pi.py), stored in common/baselines/.
    baseline = None if args.no_baseline else load_baseline(args.climate, args.period)
    report = evaluate(result, baseline)

    out_dir = HERE / 'results' / f'{args.climate.lower()}_{args.period}'
    result.to_csv(out_dir / 'timeseries.csv')
    report['daily'].to_csv(out_dir / 'daily.csv', index=False)
    report['buildings'].to_csv(out_dir / 'buildings.csv')
    pd.Series(report['summary']).to_csv(out_dir / 'summary.csv', header=['value'])
    print(f'Saved results to {out_dir.relative_to(REPO_ROOT)}')

    fig_dir = plot_all(result, report, baseline, out_dir)
    print(f'Saved figures to {fig_dir.relative_to(REPO_ROOT)}')


if __name__ == '__main__':
    main()
