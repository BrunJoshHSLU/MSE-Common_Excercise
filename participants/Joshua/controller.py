"""Your controller for Annex 96 Common Exercise 1.

Starting point: the tuned PI thermostat from common/tuned_pi.py (heat pump kept inside the comfort band,
gentle low-gain control so the LSTM building models don't oscillate) with the battery still idle.

Where to go from here:
- `battery_action`: use the batteries to track the reference r_k
  (`common.evaluation.load_reference(climate, start, n)` gives the official profile).
- `thermal_action`: pre-heat/pre-cool (thermal mass) for tracking, e.g. by shifting the setpoint.
- Gains and targets: ControllerTest(env, kp=..., ki=..., heat_target=..., cool_target=...).

Don't edit common/tuned_pi.py: it is everyone's baseline (common/baselines/).
"""

from typing import Any

from common.citylearn.citylearn import CityLearnEnv

from common.tuned_pi import TunedPIController


class ControllerTest(TunedPIController):
    """One instance controls the whole portfolio when the environment is created with
    `central_agent=True` (also works with `central_agent=False`)."""

    def __init__(self, env: CityLearnEnv, **kwargs: Any):
        super().__init__(env, **kwargs)

    def battery_action(self, i: int, building) -> float:
        """Battery action for building `i` in [-1, 1] (charge > 0). Idle for now."""
        return 0.0

    def thermal_action(self, i: int, building, action_name: str) -> float:
        """Heat pump action for building `i` in [0, 1]. Currently the tuned PI unchanged."""
        return super().thermal_action(i, building, action_name)
