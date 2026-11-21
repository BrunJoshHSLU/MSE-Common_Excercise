"""Training-month measurements and causal forecasts for the CE1 portfolio controllers."""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import lsq_linear

from common.evaluation import PERIODS, make_env
from common.tuned_pi import TunedPIController


def measurements(buildings, climate):
    """One row per building: non-HVAC net demand, HVAC electricity, outdoor and indoor C."""
    rows = []
    hvac_key = 'heating_electricity_consumption' if climate == 'VT' else 'cooling_electricity_consumption'
    for building in buildings:
        obs = building.observations(include_all=False, normalize=False)
        hvac = obs[hvac_key]
        net = obs['net_electricity_consumption'] - hvac - obs['electrical_storage_electricity_consumption']
        rows.append((net, hvac, obs['outdoor_dry_bulb_temperature'], obs['indoor_dry_bulb_temperature']))
    return np.asarray(rows, dtype=float)


def training_trace(climate):
    env = make_env(climate, 'train', central_agent=True)
    observations, _ = env.reset()
    agent = TunedPIController(env)
    trace = [measurements(env.buildings, climate)]
    while not env.terminated:
        observations, _, _, _, _ = env.step(agent.predict(observations))
        trace.append(measurements(env.buildings, climate))
    return np.stack(trace), [b.name for b in env.buildings]


def hourly_profile(trace):
    return np.stack([trace[h::24, :, :3].mean(axis=0) for h in range(24)])


def forecast(profile, observed, hour, leads, decay):
    """Hour-of-day mean plus a decaying correction from the most recent observation."""
    correction = observed[:, :3] - profile[hour]
    return profile[(hour + leads) % 24] + decay ** leads[:, None, None] * correction


@dataclass
class Model:
    profile: np.ndarray
    thermal: np.ndarray
    decay: float
    buildings: list


def fit(climate, trace, buildings):
    expected = PERIODS[climate]['train']
    if len(trace) != expected[1] - expected[0] + 1 or expected[0] % 24:
        raise ValueError('Expected the complete CE1 training month starting at midnight.')
    # Select persistence on the final seven training days using earlier days for the profile.
    cut = len(trace) - 7 * 24
    profile = hourly_profile(trace[:cut])
    leads = np.arange(1, 13)
    scale = np.maximum(trace[:cut, :, :3].std(axis=0), 0.1)
    def validation_error(decay):
        errors = []
        for t in range(cut, len(trace) - leads[-1]):
            prediction = forecast(profile, trace[t], t % 24, leads, decay)
            errors.append(np.mean(((prediction - trace[t + leads, :, :3]) / scale) ** 2))
        return np.mean(errors)
    decay = min((0.0, 0.5, 0.8, 0.95), key=validation_error)

    # One-hour temperature response per house, excluding the LSTM initialization day.
    sign = 1 if climate == 'VT' else -1
    coefficients = []
    for i in range(len(buildings)):
        prev = trace[24:-1, i, 3]
        _, power, outdoor, indoor = trace[25:, i].T
        design = np.column_stack((outdoor - prev, sign * power, np.ones_like(prev)))
        result = lsq_linear(design, indoor - prev, bounds=([0, 0, -np.inf], [1, np.inf, np.inf]))
        if not result.success:
            raise RuntimeError(f'Thermal model fit failed for {buildings[i]}')
        coefficients.append(result.x)
    return Model(hourly_profile(trace), np.asarray(coefficients), decay, buildings)
