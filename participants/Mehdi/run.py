"""Fit the final climate-specific MPC on its training month and score its test month."""

import argparse
import sys
from pathlib import Path

import torch  # Import before pandas on Windows.

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'common'))
sys.path.insert(0, str(ROOT))

import pandas as pd

from common.evaluation import BASELINES_DIR, PERIODS, SimulationResult, collect, evaluate, load_reference, make_env
from controller import Controller
from model import fit, training_trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--climate', choices=('TX', 'VT'), required=True)
    climate = parser.parse_args().climate
    torch.set_num_threads(1)
    trace, buildings = training_trace(climate)
    model = fit(climate, trace, buildings)
    start, end = PERIODS[climate]['test']
    reference = load_reference(climate, start, end - start + 1)
    env = make_env(climate, 'test', central_agent=True)
    observations, _ = env.reset()
    controller = Controller(env, climate, model, reference)
    while not env.terminated:
        observations, _, _, _, _ = env.step(controller.predict(observations))

    result = collect(env, climate, 'test')
    baseline = SimulationResult.from_csv(BASELINES_DIR / f'baseline_{climate.lower()}_test.csv')
    report = evaluate(result, baseline)
    out = HERE / 'results' / f'{climate.lower()}_test'
    result.to_csv(out / 'timeseries.csv')
    report['daily'].to_csv(out / 'daily.csv', index=False)
    report['buildings'].to_csv(out / 'buildings.csv')
    pd.Series(report['summary']).to_csv(out / 'summary.csv', header=['value'])
    print(f'Saved {climate} test results to {out}')


if __name__ == '__main__':
    main()
