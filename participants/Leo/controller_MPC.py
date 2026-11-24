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


"""Centralized battery MPC for Annex 96 Common Exercise 1.

Version 1
---------
The controller coordinates the electrical batteries of all buildings using
one centralized convex optimization problem.

The existing tuned PI controller remains responsible for heating and cooling.

At every simulation step, the controller:

1. reads the current portfolio state;
2. creates a 24-step portfolio-load forecast;
3. obtains the corresponding reference trajectory;
4. optimizes all building batteries jointly;
5. applies only the first optimized battery action;
6. repeats the optimization at the following simulation step.

This is a receding-horizon controller.

Positive battery action means charging.
Negative battery action means discharging.
"""

from typing import Any, Optional

import cvxpy as cp
import numpy as np

from common.citylearn.citylearn import CityLearnEnv
from common.evaluation import load_reference
from common.tuned_pi import TunedPIController


class Controller(TunedPIController):
    """Centralized battery MPC with tuned PI thermal control."""

    def __init__(
        self,
        env: CityLearnEnv,
        climate: str = "VT",
        period: str = "train",
        horizon: int = 24,
        minimum_soc: float = 0.10,
        maximum_soc: float = 0.90,
        target_soc: float = 0.50,
        tracking_weight: float = 1.0,
        battery_usage_weight: float = 0.001,
        terminal_soc_weight: float = 0.10,
        **kwargs: Any,
    ):
        """Initialize the centralized MPC.

        Parameters
        ----------
        env:
            Centralized CityLearn environment.

        climate:
            ``"VT"`` or ``"TX"``.

        period:
            ``"train"`` or ``"test"``.

        horizon:
            Number of future simulation steps optimized by the MPC.

        minimum_soc:
            Lower battery SOC limit, expressed as a fraction.

        maximum_soc:
            Upper battery SOC limit, expressed as a fraction.

        target_soc:
            Desired SOC at the end of the prediction horizon.

        tracking_weight:
            Weight placed on portfolio reference tracking.

        battery_usage_weight:
            Small penalty on battery charging and discharging.

        terminal_soc_weight:
            Weight placed on terminal SOC recovery.

        **kwargs:
            Additional arguments forwarded to TunedPIController.
        """

        super().__init__(env, **kwargs)

        self.env = env
        self.climate = climate.upper()
        self.period = period.lower()
        self.horizon = int(horizon)

        self.minimum_soc = float(minimum_soc)
        self.maximum_soc = float(maximum_soc)
        self.target_soc = float(target_soc)

        self.tracking_weight = float(tracking_weight)
        self.battery_usage_weight = float(battery_usage_weight)
        self.terminal_soc_weight = float(terminal_soc_weight)

        if self.climate not in {"VT", "TX"}:
            raise ValueError(
                f"Unknown climate {self.climate!r}. Use 'VT' or 'TX'."
            )

        if self.period not in {"train", "test"}:
            raise ValueError(
                f"Unknown period {self.period!r}. Use 'train' or 'test'."
            )

        if self.horizon <= 0:
            raise ValueError("MPC horizon must be greater than zero.")

        if not 0.0 <= self.minimum_soc < self.maximum_soc <= 1.0:
            raise ValueError(
                "SOC limits must satisfy "
                "0 <= minimum_soc < maximum_soc <= 1."
            )

        self.number_of_buildings = len(self.env.buildings)

        # The centralized MPC calculates one battery action for each
        # building. battery_action() later returns the stored value.
        self.current_battery_actions = np.zeros(
            self.number_of_buildings,
            dtype=float,
        )

        # Previous portfolio demand is used as the first simple load forecast.
        self.previous_portfolio_power_kw: Optional[float] = None

        # Diagnostics.
        self.mpc_solve_count = 0
        self.mpc_failure_count = 0
        self.last_solver_status = "not_solved"
        self.last_objective_value = np.nan

        self.reference_profile = self._load_reference_profile()

        print(
            "\nCentralized battery MPC initialized:"
            f"\n  climate: {self.climate}"
            f"\n  period: {self.period}"
            f"\n  buildings: {self.number_of_buildings}"
            f"\n  horizon: {self.horizon}"
            f"\n  reference values: {len(self.reference_profile)}"
            "\n"
        )

    def predict(self, observations, deterministic: bool = True):
        """Calculate centralized battery actions and all thermal actions.

        The MPC is solved once before the inherited TunedPIController builds
        the complete CityLearn action vector. During that inherited process,
        battery_action() returns the stored first MPC action for each building.

        If the MPC cannot produce a valid solution, every battery remains
        idle for that simulation step.
        """

        del deterministic

        time_step = self._get_time_step()

        try:
            actions = self._solve_centralized_battery_mpc(
                time_step=time_step,
            )

            if actions is None:
                self._use_safe_battery_fallback(
                    reason="optimizer returned no valid solution",
                )
            else:
                self.current_battery_actions = np.clip(
                    np.asarray(actions, dtype=float),
                    -1.0,
                    1.0,
                )

        except Exception as error:
            self._use_safe_battery_fallback(
                reason=f"{type(error).__name__}: {error}",
            )

        # The parent predicts the complete CityLearn action vector. It calls
        # this class's battery_action() and thermal_action() methods.
        return super().predict(observations)

    def battery_action(self, i: int, building) -> float:
        """Return the stored first MPC battery action for building ``i``."""

        del building

        if i < 0 or i >= len(self.current_battery_actions):
            return 0.0

        action = self.current_battery_actions[i]

        if not np.isfinite(action):
            return 0.0

        return float(np.clip(action, -1.0, 1.0))

    def thermal_action(
        self,
        i: int,
        building,
        action_name: str,
    ) -> float:
        """Retain the shared tuned PI controller for thermal control."""

        return super().thermal_action(i, building, action_name)

    def _solve_centralized_battery_mpc(
        self,
        time_step: int,
    ) -> Optional[np.ndarray]:
        """Solve one centralized 24-step battery optimization."""

        number_of_buildings = self.number_of_buildings
        effective_horizon = self._effective_horizon(time_step)

        if effective_horizon <= 0:
            return np.zeros(number_of_buildings, dtype=float)

        soc_initial = self._read_battery_soc()
        capacities_kwh = self._read_battery_capacities()
        maximum_actions = self._read_maximum_battery_actions()

        portfolio_forecast_kw = self._create_portfolio_load_forecast(
            horizon=effective_horizon,
        )

        reference_forecast_kw = self._get_reference_forecast(
            time_step=time_step,
            horizon=effective_horizon,
        )

        # Separate nonnegative charging and discharging actions make the
        # optimization continuous and convex.
        charge_action = cp.Variable(
            (effective_horizon, number_of_buildings),
            nonneg=True,
        )

        discharge_action = cp.Variable(
            (effective_horizon, number_of_buildings),
            nonneg=True,
        )

        soc = cp.Variable(
            (effective_horizon + 1, number_of_buildings),
        )

        constraints = [
            soc[0, :] == soc_initial,
            charge_action <= maximum_actions,
            discharge_action <= maximum_actions,
        ]

        charge_efficiency = 0.95
        discharge_efficiency = 0.95

        # Approximate action-to-energy conversion:
        # action 1.0 corresponds to one battery-capacity unit over one
        # simulation step. This conversion must later be checked against the
        # exact local CityLearn electrical-storage implementation.
        for prediction_step in range(effective_horizon):
            soc_change = (
                charge_efficiency
                * charge_action[prediction_step, :]
                - discharge_action[prediction_step, :]
                / discharge_efficiency
            )

            constraints.append(
                soc[prediction_step + 1, :]
                == soc[prediction_step, :] + soc_change
            )

            constraints.append(
                soc[prediction_step + 1, :] >= self.minimum_soc
            )

            constraints.append(
                soc[prediction_step + 1, :] <= self.maximum_soc
            )

        # Convert normalized actions approximately into portfolio power.
        #
        # Positive action:
        #     battery charging increases portfolio electricity demand.
        #
        # Negative action:
        #     battery discharging decreases portfolio electricity demand.
        battery_power_kw = cp.multiply(
            charge_action - discharge_action,
            capacities_kwh.reshape(1, -1),
        )

        portfolio_battery_power_kw = cp.sum(
            battery_power_kw,
            axis=1,
        )

        predicted_portfolio_power_kw = (
            portfolio_forecast_kw
            + portfolio_battery_power_kw
        )

        tracking_error_kw = (
            predicted_portfolio_power_kw
            - reference_forecast_kw
        )

        tracking_scale_kw = max(
            1.0,
            float(np.nanmean(np.abs(reference_forecast_kw))),
        )

        normalized_tracking_error = (
            tracking_error_kw / tracking_scale_kw
        )

        tracking_cost = (
            self.tracking_weight
            * cp.sum_squares(normalized_tracking_error)
        )

        battery_usage_cost = (
            self.battery_usage_weight
            * cp.sum(charge_action + discharge_action)
        )

        terminal_soc_cost = (
            self.terminal_soc_weight
            * cp.sum_squares(
                soc[effective_horizon, :] - self.target_soc
            )
        )

        objective = cp.Minimize(
            tracking_cost
            + battery_usage_cost
            + terminal_soc_cost
        )

        problem = cp.Problem(
            objective,
            constraints,
        )

        solved = self._solve_problem(problem)

        self.mpc_solve_count += 1
        self.last_solver_status = str(problem.status)

        if not solved:
            self.mpc_failure_count += 1
            return None

        if charge_action.value is None or discharge_action.value is None:
            self.mpc_failure_count += 1
            return None

        first_action = (
            np.asarray(charge_action.value[0, :], dtype=float)
            - np.asarray(discharge_action.value[0, :], dtype=float)
        )

        if not np.all(np.isfinite(first_action)):
            self.mpc_failure_count += 1
            return None

        self.last_objective_value = float(problem.value)

        first_action = np.clip(
            first_action,
            -maximum_actions,
            maximum_actions,
        )

        # Print compact diagnostics once per day.
        if time_step % 24 == 0:
            print(
                "MPC "
                f"step={time_step:4d}, "
                f"status={problem.status}, "
                f"objective={problem.value:.6f}, "
                f"portfolio_forecast="
                f"{portfolio_forecast_kw[0]:.2f} kW, "
                f"reference={reference_forecast_kw[0]:.2f} kW, "
                f"battery_action_sum={np.sum(first_action):.3f}"
            )

        return first_action

    def _solve_problem(self, problem: cp.Problem) -> bool:
        """Solve the CVXPY problem with an available convex solver."""

        accepted_statuses = {
            cp.OPTIMAL,
            cp.OPTIMAL_INACCURATE,
        }

        installed_solvers = set(cp.installed_solvers())

        solver_order = [
            "CLARABEL",
            "OSQP",
            "SCS",
        ]

        for solver_name in solver_order:
            if solver_name not in installed_solvers:
                continue

            try:
                problem.solve(
                    solver=solver_name,
                    verbose=False,
                    warm_start=True,
                )
            except cp.error.SolverError:
                continue

            if problem.status in accepted_statuses:
                return True

        return False

    def _load_reference_profile(self) -> np.ndarray:
        """Load the official CE reference profile for this episode."""

        start_index = 0
        expected_length = self._episode_length()

        reference = load_reference(
            self.climate,
            start_index,
            expected_length,
        )

        # load_reference may return a Series, DataFrame, list or ndarray.
        if hasattr(reference, "to_numpy"):
            reference = reference.to_numpy()

        reference = np.asarray(reference, dtype=float).squeeze()

        if reference.ndim != 1:
            reference = reference.reshape(-1)

        if len(reference) == 0:
            raise ValueError("The loaded reference profile is empty.")

        if not np.all(np.isfinite(reference)):
            raise ValueError(
                "The reference profile contains non-finite values."
            )

        return reference

    def _period_start_timestamp(self) -> str:
        """Return the first timestamp for the requested CE episode."""

        starts = {
            ("VT", "train"): "2018-01-01",
            ("VT", "test"): "2018-02-01",
            ("TX", "train"): "2018-06-01",
            ("TX", "test"): "2018-07-01",
        }

        return starts[(self.climate, self.period)]

    def _episode_length(self) -> int:
        """Return the number of simulation steps in the current episode."""

        candidates = [
            "time_steps",
            "episode_time_steps",
        ]

        for attribute_name in candidates:
            if not hasattr(self.env, attribute_name):
                continue

            value = getattr(self.env, attribute_name)

            try:
                length = len(value)
            except TypeError:
                try:
                    length = int(value)
                except (TypeError, ValueError):
                    continue

            if length > 0:
                return length

        # CE monthly episode defaults.
        lengths = {
            ("VT", "train"): 744,
            ("VT", "test"): 672,
            ("TX", "train"): 720,
            ("TX", "test"): 744,
        }

        return lengths[(self.climate, self.period)]

    def _effective_horizon(self, time_step: int) -> int:
        """Shorten the MPC horizon near the end of the episode."""

        remaining_reference_steps = len(self.reference_profile) - time_step

        return max(
            0,
            min(
                self.horizon,
                remaining_reference_steps,
            ),
        )

    def _get_reference_forecast(
        self,
        time_step: int,
        horizon: int,
    ) -> np.ndarray:
        """Extract the reference trajectory for the MPC horizon."""

        end_step = min(
            time_step + horizon,
            len(self.reference_profile),
        )

        forecast = np.asarray(
            self.reference_profile[time_step:end_step],
            dtype=float,
        )

        if len(forecast) < horizon:
            padding_value = (
                forecast[-1]
                if len(forecast) > 0
                else self.reference_profile[-1]
            )

            forecast = np.pad(
                forecast,
                pad_width=(0, horizon - len(forecast)),
                mode="constant",
                constant_values=padding_value,
            )

        return forecast

    def _create_portfolio_load_forecast(
        self,
        horizon: int,
    ) -> np.ndarray:
        """Create the first simple persistence forecast.

        Version 1 assumes that the current portfolio power remains constant
        over the prediction horizon.

        This is intentionally simple. It will later be replaced with a
        profile-based or data-driven forecast.
        """

        current_portfolio_power_kw = self._read_portfolio_power()

        if not np.isfinite(current_portfolio_power_kw):
            if self.previous_portfolio_power_kw is None:
                current_portfolio_power_kw = 0.0
            else:
                current_portfolio_power_kw = (
                    self.previous_portfolio_power_kw
                )

        self.previous_portfolio_power_kw = (
            current_portfolio_power_kw
        )

        return np.full(
            horizon,
            current_portfolio_power_kw,
            dtype=float,
        )

    def _read_portfolio_power(self) -> float:
        """Read the most recent aggregate net electricity consumption."""

        total_power_kw = 0.0
        valid_buildings = 0

        for building in self.env.buildings:
            value = self._last_finite_value(
                getattr(
                    building,
                    "net_electricity_consumption",
                    None,
                )
            )

            if value is None:
                continue

            total_power_kw += value
            valid_buildings += 1

        if valid_buildings > 0:
            return float(total_power_kw)

        # At the initial step, net consumption may not yet be available.
        # Fall back to non-shiftable load if exposed by the building.
        for building in self.env.buildings:
            energy_simulation = getattr(
                building,
                "energy_simulation",
                None,
            )

            if energy_simulation is None:
                continue

            value = self._last_finite_value(
                getattr(
                    energy_simulation,
                    "non_shiftable_load",
                    None,
                )
            )

            if value is not None:
                total_power_kw += value
                valid_buildings += 1

        if valid_buildings > 0:
            return float(total_power_kw)

        return float("nan")

    def _read_battery_soc(self) -> np.ndarray:
        """Read current battery SOC for all buildings."""

        soc_values = []

        for building in self.env.buildings:
            storage = getattr(
                building,
                "electrical_storage",
                None,
            )

            if storage is None:
                soc_values.append(self.target_soc)
                continue

            soc_value = self._last_finite_value(
                getattr(storage, "soc", None)
            )

            if soc_value is None:
                soc_values.append(self.target_soc)
                continue

            capacity = self._positive_scalar(
                getattr(storage, "capacity", None),
                fallback=1.0,
            )

            # Some CityLearn versions expose SOC as stored energy in kWh,
            # while others may expose it directly as a fraction.
            if soc_value > 1.0 + 1.0e-6:
                soc_fraction = soc_value / capacity
            else:
                soc_fraction = soc_value

            soc_values.append(
                float(
                    np.clip(
                        soc_fraction,
                        self.minimum_soc,
                        self.maximum_soc,
                    )
                )
            )

        return np.asarray(soc_values, dtype=float)

    def _read_battery_capacities(self) -> np.ndarray:
        """Read battery capacities in kWh."""

        capacities = []

        for building in self.env.buildings:
            storage = getattr(
                building,
                "electrical_storage",
                None,
            )

            if storage is None:
                capacities.append(1.0)
                continue

            capacities.append(
                self._positive_scalar(
                    getattr(storage, "capacity", None),
                    fallback=1.0,
                )
            )

        return np.asarray(capacities, dtype=float)

    def _read_maximum_battery_actions(self) -> np.ndarray:
        """Return normalized battery-action limits for all buildings."""

        limits = []

        for building in self.env.buildings:
            storage = getattr(
                building,
                "electrical_storage",
                None,
            )

            if storage is None:
                limits.append(0.0)
                continue

            capacity = self._positive_scalar(
                getattr(storage, "capacity", None),
                fallback=1.0,
            )

            nominal_power = self._positive_scalar(
                getattr(storage, "nominal_power", None),
                fallback=capacity,
            )

            # Approximate normalized action limit from nominal power and
            # energy capacity. CityLearn still clips actions to [-1, 1].
            normalized_limit = min(
                1.0,
                nominal_power / capacity,
            )

            limits.append(normalized_limit)

        return np.asarray(limits, dtype=float)

    def _get_time_step(self) -> int:
        """Return the current CityLearn simulation-step index."""

        value = getattr(self.env, "time_step", 0)

        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _use_safe_battery_fallback(self, reason: str) -> None:
        """Use idle batteries if MPC data or optimization fail."""

        self.current_battery_actions = np.zeros(
            self.number_of_buildings,
            dtype=float,
        )

        self.mpc_failure_count += 1
        self.last_solver_status = "fallback"

        time_step = self._get_time_step()

        # Avoid printing the same kind of message 744 times.
        if time_step < 3 or time_step % 24 == 0:
            print(
                f"MPC fallback at step {time_step}: {reason}. "
                "All batteries remain idle."
            )

    @staticmethod
    def _last_finite_value(values) -> Optional[float]:
        """Return the most recent finite scalar from a scalar or sequence."""

        if values is None:
            return None

        try:
            array = np.asarray(values, dtype=float).reshape(-1)
        except (TypeError, ValueError):
            return None

        if array.size == 0:
            return None

        finite_values = array[np.isfinite(array)]

        if finite_values.size == 0:
            return None

        return float(finite_values[-1])

    @staticmethod
    def _positive_scalar(value, fallback: float) -> float:
        """Convert an attribute into a finite positive scalar."""

        try:
            array = np.asarray(value, dtype=float).reshape(-1)
        except (TypeError, ValueError):
            return float(fallback)

        if array.size == 0:
            return float(fallback)

        finite_values = array[np.isfinite(array)]

        if finite_values.size == 0:
            return float(fallback)

        scalar = float(finite_values[-1])

        if scalar <= 0.0:
            return float(fallback)

        return scalar