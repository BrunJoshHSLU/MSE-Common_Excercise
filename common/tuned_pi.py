"""Tuned PI thermostat: the shared CE1 baseline controller of this workspace.

`common/evaluation.py` (run_baseline / load_baseline) simulates this controller to produce the
baseline runs in common/baselines/. Participants can also inherit from it as a starting point
(see participants/_template/controller.py).

Why not the official baseline
-----------------------------
The original CE1 baseline (citylearn PITemperatureController, kp=0.2, deadband 0.5 K with integral
reset) oscillates on the CE1 LSTM building models: at full power those models gain ~10 K per hour,
so the controller bangs between 0 and 100 % heating and drives houses to -17..62 degC (VT January,
median discomfort ~1500 K h). It also follows the ResStock setpoints, several of which (17.2 degC)
lie below the 20 degC comfort bound.

This controller
---------------
- aims inside the CE1 comfort band: max(heating setpoint, 21 degC), min(cooling setpoint, 25 degC)
- low gains (kp=0.02, ki=0.005), no deadband, conditional-integration anti-windup
- leaves the battery idle (action 0), i.e. PV only, no battery use

The reference r_k is NOT affected: it is still the official district_target.csv.

If this file changes, delete common/baselines/*.csv so the baselines are re-simulated, and tell
the group: everyone's comparison values change.
"""

from __future__ import annotations

from typing import Any, List

import numpy as np

from citylearn.agents.base import Agent  # needs common/ on sys.path (run.py takes care of that)


class TunedPIController(Agent):
    """Low-gain PI on each building's heat pump; battery idle.

    Works with central_agent=True and False. Building states are read from each building's own
    observations, because the flattened central-agent observation vector drops duplicated names
    (in TX, `indoor_dry_bulb_temperature` appears only once for all 25 buildings).
    """

    def __init__(self, env, kp: float = 0.02, ki: float = 0.005,
                 heat_target: float = 21.0, cool_target: float = 25.0, **kwargs: Any):
        super().__init__(env, **kwargs)
        self.kp, self.ki = kp, ki
        self.heat_target, self.cool_target = heat_target, cool_target
        self._integral = {}

        # Actions are produced per building, in each building's active_actions order.
        # Check that this matches the environment's action layout.
        per_building = [list(b.active_actions) for b in self.buildings]
        expected = [sum(per_building, [])] if self.env.unwrapped.central_agent else per_building
        if [list(a) for a in self.action_names] != expected:
            raise ValueError('Unexpected action layout; cannot map actions to buildings.')

    @property
    def buildings(self):
        return self.env.unwrapped.buildings

    def reset(self):
        super().reset()
        self._integral = {}

    # ---- building-level decisions (override these in your own controller) -----------------

    def thermal_action(self, i: int, building, action_name: str) -> float:
        """Heat pump action in [0, 1] for building `i`."""
        o = building.observations(include_all=False, normalize=False)
        t_in = o['indoor_dry_bulb_temperature']
        if action_name == 'heating_device':
            setpoint = max(o['indoor_dry_bulb_temperature_heating_set_point'], self.heat_target)
            return self._pi((i, 'heat'), setpoint - t_in)
        if action_name == 'cooling_device':
            setpoint = min(o['indoor_dry_bulb_temperature_cooling_set_point'], self.cool_target)
            return self._pi((i, 'cool'), t_in - setpoint)
        return 0.0

    def battery_action(self, i: int, building) -> float:
        """Battery action in [-1, 1] (fraction of capacity per step). Idle here."""
        return 0.0

    # ---- plumbing --------------------------------------------------------------------------

    def _pi(self, key, error: float) -> float:
        integral = self._integral.get(key, 0.0)
        u = self.kp * error + self.ki * integral
        # Conditional integration: stop integrating while saturated in the same direction.
        if 0.0 < u < 1.0 or (u >= 1.0 and error < 0) or (u <= 0.0 and error > 0):
            integral += error
        self._integral[key] = integral
        return float(np.clip(self.kp * error + self.ki * integral, 0.0, 1.0))

    def building_actions(self, i: int, building) -> List[float]:
        return [self.battery_action(i, building) if name == 'electrical_storage'
                else self.thermal_action(i, building, name)
                for name in building.active_actions]

    def predict(self, observations: List[List[float]], deterministic: bool = None) -> List[List[float]]:
        per_building = [self.building_actions(i, b) for i, b in enumerate(self.buildings)]
        actions = [sum(per_building, [])] if self.env.unwrapped.central_agent else per_building
        # Battery limits differ per building: clip to the allowed range.
        actions = [np.clip(a, s.low, s.high).tolist() for a, s in zip(actions, self.action_space)]
        self.actions = actions
        self.next_time_step()
        return actions
