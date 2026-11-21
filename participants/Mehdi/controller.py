"""Centralized shooting MPC for the hourly CE1 residential portfolios."""

from itertools import product

import numpy as np

from common.evaluation import COMFORT_BOUNDS, PERIODS
from common.tuned_pi import TunedPIController
from model import forecast, measurements


def battery_dispatch(net, energy, capacity, floor, power, efficiency, target, fraction):
    """Allocate district correction in proportion to feasible power at each house."""
    charge_limit = np.minimum(power, np.maximum(capacity - energy, 0) / efficiency)
    # Surplus PV can charge behind the meter without changing measured import.
    solar_charge = np.minimum(np.maximum(-net, 0), charge_limit)
    imports = np.maximum(net, 0)
    discharge = np.minimum(np.minimum(power, imports), np.maximum(energy - floor, 0) * efficiency)
    # A site still exporting after solar charging has no spare charging power.
    headroom = charge_limit - solar_charge
    error = target - imports.sum(axis=1, keepdims=True)
    available = np.where(error >= 0, headroom, discharge)
    share = np.clip(error / np.maximum(available.sum(axis=1, keepdims=True), 1e-9), -1, 1)
    return solar_charge + fraction * share * available


def scheduled_import(net_path, targets, energy, capacity, floor, power, efficiency, retention):
    """Return the first district grid-import target from a discretized storage model.

    Device-level dispatch subsequently enforces local power, charge and no-export limits.
    The state is usable stored energy above the minimum state of charge, in kWh.
    """
    usable = np.maximum(capacity - floor, 0)
    total = usable.sum()
    imports = np.maximum(net_path, 0)
    if total <= 1e-9:
        return imports[0].sum()
    initial = np.clip(energy - floor, 0, usable).sum()
    states = np.unique(np.append(np.linspace(0, total, 101), initial))
    eta = np.average(efficiency, weights=usable)
    keep = np.average(retention, weights=usable)
    change = states[None, :] - keep * states[:, None]
    flow = np.where(change >= 0, change / eta, change * eta)
    value = np.zeros(len(states))
    start = np.searchsorted(states, initial)
    for t in reversed(range(len(targets))):
        surplus = np.minimum(np.maximum(-net_path[t], 0), power).sum()
        discharge_limit = np.minimum(imports[t], power).sum()
        # Free charging from surplus only offsets positive battery input.
        grid = imports[t].sum() + np.where(flow > 0, np.maximum(flow - surplus, 0), flow)
        feasible = (flow >= -discharge_limit - 1e-8) & (flow <= power.sum() + 1e-8)
        cost = np.where(feasible, (grid - targets[t]) ** 2 + value[None, :], np.inf)
        if t == 0:
            return grid[start, np.argmin(cost[start])]
        value = cost.min(axis=1)


class Controller(TunedPIController):
    """Rank feasible multi-hour dispatch policies, then execute the best first move."""

    horizon = 12

    def __init__(self, env, climate, model, reference):
        super().__init__(env)
        self.climate = climate
        self.model = model
        self.reference = reference
        self.sign = 1 if climate == 'VT' else -1
        self.device = 'heating_device' if climate == 'VT' else 'cooling_device'
        self.low, self.high = COMFORT_BOUNDS[PERIODS[climate]['season']]
        self.cooling = climate == 'TX'
        if model.buildings != [b.name for b in self.buildings]:
            raise ValueError('Training and evaluation portfolios differ.')
        if any(set(b.active_actions) != {'electrical_storage', self.device} for b in self.buildings):
            raise ValueError('Expected one battery and one seasonal heat pump per home.')
        stores = [b.electrical_storage for b in self.buildings]
        self.capacity = np.array([s.capacity for s in stores])
        self.floor = self.capacity * np.array([1 - s.depth_of_discharge for s in stores])
        self.power = np.array([s.nominal_power for s in stores])
        self.retention = np.array([1 - s.loss_coefficient for s in stores])
        self.rating = np.array([getattr(b, self.device).nominal_power for b in self.buildings])
        # Portfolio battery correction fractions now/later and near-term heat-pump offsets.
        self.candidates = np.array(list(product((0.0, 0.5, 1.0), (0.5, 1.0), (-0.04, 0.0, 0.04))))

    def predict(self, observations, deterministic=True):
        buildings = self.buildings
        t = self.env.unwrapped.time_step
        observed = measurements(buildings, self.climate)
        leads = np.arange(1, min(self.horizon, len(self.reference) - 1 - t) + 1)
        hours = (t + leads) % 24
        cooling_margin = self.cooling & (hours >= 15) & (hours <= 18)
        predicted = forecast(self.model.profile, observed, t % 24, leads, self.model.decay)
        pi = np.array([self.thermal_action(i, b, self.device) for i, b in enumerate(buildings)])
        stores = [b.electrical_storage for b in buildings]
        energy = np.array([s.soc[t] * s.capacity for s in stores])
        capacity = np.array([s.degraded_capacity for s in stores])
        efficiency = np.array([s.round_trip_efficiency for s in stores])
        temperature = observed[:, 3]
        conductance, response, drift = self.model.thermal.T
        if t > 0:
            last = np.array([b.indoor_dry_bulb_temperature[t - 1] for b in buildings])
            # Aim for the band midpoint over several hours; damp the observed trend.
            midpoint = (self.low + self.high) / 2
            desired = 0.35 * (midpoint - temperature)
            change = self.sign * (desired - 0.55 * (temperature - last))
            delta = np.clip(change / np.maximum(response * self.rating, 2.0), -0.10, 0.10)
            pi = np.clip(0.5 * pi + 0.5 * (observed[:, 1] / self.rating + delta), 0, 1)
            if self.cooling and 13 <= hours[0] <= 18:
                # A half-step of measured trend concentrates the intervention on
                # homes likely to need it, including homes already out of band.
                near_future = temperature + 0.5 * np.clip(temperature - last, -2, 2)
                exposure = np.maximum(near_future - (self.high - 1.0), 0)
                extra = np.minimum(0.06, 0.5 * exposure / np.maximum(response * self.rating, 2.0))
                pi = np.minimum(1, pi + extra)
        early, later, shift = self.candidates.T[:, :, None]
        scores = np.zeros(len(early))
        net_path = []
        for step, (net, hvac, outdoor) in enumerate(predicted.transpose(0, 2, 1)):
            # The current PI command anchors the first move. Later predictions
            # use the training profile and feedback relative to today's temperature.
            nominal = pi * self.rating if step == 0 else np.maximum(hvac, 0)
            feedback = self.sign * 0.02 * (observed[:, 3] - temperature) if step else 0
            offset = shift if step < 3 else 0
            thermal = np.clip(nominal + (offset + feedback) * self.rating, 0, self.rating)
            # An offset must not worsen an existing comfort violation.
            recovering = ((temperature < self.low) & (self.sign * offset < 0)) | (
                (temperature > self.high) & (self.sign * offset > 0))
            thermal = np.where(recovering, np.clip(nominal + feedback * self.rating, 0, self.rating), thermal)
            net = net + thermal
            if self.cooling:
                net_path.append(net)
            energy = energy * self.retention
            if self.cooling and step == 0:
                retained = energy.copy()
            target = self.reference[t + step + 1]
            dispatch = battery_dispatch(net, energy, capacity, self.floor, self.power,
                                        efficiency, target, early if step < 3 else later)
            energy = energy + np.where(dispatch >= 0, dispatch * efficiency, dispatch / efficiency)
            temperature = temperature + conductance * (outdoor - temperature) + self.sign * response * thermal + drift
            upper = self.high - float(cooling_margin[step])
            violation = np.maximum(self.low - temperature, 0) + np.maximum(temperature - upper, 0)
            error_per_home = (np.maximum(net + dispatch, 0).sum(axis=1) - target) / len(buildings)
            scores += error_per_home ** 2 + 4.0 * (violation ** 2).mean(axis=1)
            if step == 0:
                first_battery = dispatch / self.capacity
                first_heat = np.broadcast_to(thermal / self.rating, first_battery.shape)
        best = np.argmin(scores)
        if self.cooling:
            net_forecast = np.stack([net[best] for net in net_path])
            initial = np.array([s.soc[t] * s.capacity for s in stores])
            target = scheduled_import(net_forecast, self.reference[t + leads], initial, capacity,
                                      self.floor, self.power, efficiency, self.retention)
            first_battery[best] = battery_dispatch(net_forecast[:1], retained, capacity, self.floor,
                                                   self.power, efficiency, target, 1.0)[0] / self.capacity
        actions = []
        for i, building in enumerate(buildings):
            commands = {'electrical_storage': first_battery[best, i], self.device: first_heat[best, i]}
            actions.extend(commands[name] for name in building.active_actions)
        space = self.action_space[0]
        bounded = [np.clip(actions, space.low, space.high).tolist()]
        self.actions = bounded
        self.next_time_step()
        return bounded
