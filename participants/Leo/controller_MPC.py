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

#################################################################################################


"""Centralized MPC controller for Annex 96 Common Exercise 1.

Development strategy
--------------------
Version 0:
    - Keep the tuned PI controller for heating/cooling.
    - Keep the batteries idle.
    - Verify that this separate controller reproduces the baseline.

Version 1:
    - Add centralized MPC for all building batteries.
    - Use a 24-hour receding prediction horizon.
    - Optimize portfolio reference tracking.
    - Keep thermal control on the tuned PI controller.

Version 2:
    - Extend the MPC to thermal flexibility.
    - Include indoor-temperature dynamics and comfort constraints.

The shared common/tuned_pi.py is not modified.
"""

from typing import Any

from common.citylearn.citylearn import CityLearnEnv
from common.tuned_pi import TunedPIController


class Controller(TunedPIController):
    """Centralized MPC controller for the complete building portfolio.

    CityLearn creates one instance of this controller when the environment
    uses central_agent=True.

    The first development version deliberately reproduces the tuned PI
    baseline. MPC functionality will be added incrementally while preserving
    a known working controller.
    """

    def __init__(
        self,
        env: CityLearnEnv,
        horizon: int = 24,
        **kwargs: Any,
    ):
        """Initialize the controller.

        Parameters
        ----------
        env:
            CityLearn environment containing the complete building portfolio.

        horizon:
            MPC prediction horizon in simulation time steps.
            The initial value is 24, corresponding to 24 hours if the
            simulation uses hourly time steps.

        **kwargs:
            Additional arguments forwarded to TunedPIController.
        """

        super().__init__(env, **kwargs)

        self.env = env
        self.horizon = horizon

        # ------------------------------------------------------------------
        # MPC DEVELOPMENT PLACEHOLDERS
        # ------------------------------------------------------------------
        #
        # These attributes will later contain the information required by
        # the centralized MPC.
        #
        # Examples:
        #
        # self.reference_profile
        # self.battery_parameters
        # self.load_forecasts
        # self.mpc_problem
        #
        # They are intentionally not implemented yet. The first objective is
        # to verify that controller_mpc.py correctly interfaces with the
        # existing CityLearn setup.
        # ------------------------------------------------------------------

    def battery_action(self, i: int, building) -> float:
        """Return the battery action for building ``i``.

        CityLearn expects an action in [-1, 1]:

            action > 0 : charge battery
            action = 0 : battery idle
            action < 0 : discharge battery

        Version 0:
            The battery remains idle, exactly as in the provided baseline.

        Version 1:
            This method will return the first battery action calculated by
            the centralized MPC.

        Important:
            The MPC must be solved jointly for the complete portfolio rather
            than independently inside this method for every building.
            Therefore, the future MPC solution will be calculated once per
            simulation time step and stored before battery_action() is called.
        """

        return 0.0

    def thermal_action(
        self,
        i: int,
        building,
        action_name: str,
    ) -> float:
        """Return the thermal action for building ``i``.

        For the first MPC implementation, thermal control stays exactly as
        defined by the shared tuned PI controller.

        This isolates battery MPC development from thermal-model development.
        """

        return super().thermal_action(i, building, action_name)
