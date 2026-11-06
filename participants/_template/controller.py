"""Your controller for Annex 96 Common Exercise 1.

Copy this whole folder to participants/<firstname_lastname>/ and build your approach
(RBC, MPC, RL, ...) here.

Starting point: the tuned PI thermostat from common/tuned_pi.py, which is also everyone's
baseline (common/baselines/). Unchanged, this controller reproduces the baseline exactly,
with the battery idle.

Where to go from here:
- `battery_action`: use the batteries to track the reference r_k
  (`common.evaluation.load_reference(climate, start, n)` gives the official profile).
- `thermal_action`: pre-heat/pre-cool (thermal mass) for tracking, e.g. by shifting the setpoint.
- Gains and targets: Controller(env, kp=..., ki=..., heat_target=..., cool_target=...).
- Or replace `predict` completely: any citylearn Agent works with run.py.

Don't edit common/tuned_pi.py: it defines the baseline for the whole group.
"""

from typing import Any

from common.citylearn.citylearn import CityLearnEnv

from common.tuned_pi import TunedPIController


class Controller(TunedPIController):
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
