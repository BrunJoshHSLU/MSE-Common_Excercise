"""Central model predictive controller (MPC) for Annex 96 Common Exercise 1.

What it does
------------
Every hour the controller solves ONE convex quadratic program (QP) for all buildings of the portfolio
over the next H = 24 hours, applies the first hour of the plan and repeats one hour later with new
measurements (receding horizon). Per building i and horizon hour k it decides

    u_ik           heat-pump power as a fraction of nominal power (0..1)  -> heating/cooling action
    ch_ik, dis_ik  battery charge / discharge energy [kWh]              -> battery action (ch - dis) / capacity

so that the district grid import follows the day-ahead reference r_k while every building stays inside
the CE1 comfort band (VT 20-24 degC heating, TX 22-26 degC cooling).

Hourly loop (plan / predict)
  1. measure indoor temperatures, battery state of charge and hvac mode
  2. update the per-building temperature bias (measured - predicted temperature)
  3. forecast weather, plug/DHW loads, PV, uncontrolled heating/cooling demand and the reference
  4. write them into the QP parameters (the QP is built once and is DPP: CVXPY never recompiles it)
  5. solve with OSQP, then CLARABEL, then the CVXPY default solver
  6. apply the first hour; if no solver succeeds, the tuned PI controls the heat pumps for this hour
     and the batteries stay idle

Optimisation problem (i = building, k = horizon hour)

    min  w_track     * sum_k ((sum_i y_ik - r_k) / r_mean)^2         follow the reference (NMBE / CV-RMSE)
       + w_comfort   * sum s_ik + w_comfort_sq * sum s_ik^2          comfort-band violation [K]
       + w_fair      * max_i sum_k s_ik                              worst building (fairness)
       + w_du        * sum (u_ik - u_i,k-1)^2                        smooth heat-pump operation
       + w_bat       * sum (ch_ik + dis_ik)                          battery throughput (losses, wear)
       + sum_k w_sigma_k * sum_i c_ik + w_sigma_sq * sum c_ik^2      export slack, see "Grid import"
       + w_term      * sum_i (E_iH / cap_i - soc_target)^2           do not empty the battery at the horizon end

    s.t. T_i,k+1 = a1_i T_ik + a2_i T_i,k-1 + G_ik u_ik + G1_ik u_i,k-1 + D_ik   thermal model (per-building ARX)
         E_i,k+1 = (1 - loss_i) (E_ik + eta_i ch_ik - dis_ik / eta_i)           battery (CityLearn equation)
         p_ik    = base_ik + P_hp,i u_ik + ch_ik - dis_ik    net load, base = plug + DHW + uncontrolled HVAC - PV
         y_ik    = p_ik + c_ik >= 0,  c_ik >= 0                     grid import, clipped at 0 per building
         T_min + delta_i - s_ik <= T_ik <= T_max - delta_i + s_ik,  s_ik >= 0   soft band with margin delta_i
         0 <= u_ik <= 1 when the hvac mode allows heating (cooling), else u_ik = 0
         ch + dis <= nominal power and <= capacity-power curve,  E_min_i <= E_ik <= cap_i

Model parts
  Thermal model   T[k] = a1 T[k-1] + a2 T[k-2] + b0 Q[k] + b1 Q[k-1] + b_o T_out[k] + b_dir I_dir[k]
                         + b_dif I_dif[k] + hour-of-day terms + const   (Q = delivered heat, cooling < 0),
                  fitted per building on the TRAINING month (models/thermal_<c>_<identification>.json).
                  In the QP: G_ik = +-b0 * COP_i(T_out forecast) * P_hp,i [K per unit u]; D_ik = weather,
                  hour terms, constant, uncontrolled demand and the bias [K].
  Bias filter     bias <- bias_leak * bias + bias_gain * (T_measured - T_predicted), clipped to
                  +-bias_clip_rmse * one-step RMSE; enters horizon hour j as bias * bias_decay^j;
                  frozen while CityLearn's LSTM warms up (first ~15 h, actions are ignored there).
  Battery         per-battery parameters from the environment; efficiency at bat_eff_power_frac of
                  nominal power; the concave capacity-power curve is exact as one linear constraint per
                  segment, evaluated at the SoC at the start of the hour.
  Grid import     the KPI counts each building's import clipped at 0. The slack c lifts negative net load
                  to 0. Its price w_sigma_k is sigma_factor times the tracking gain of one extra kWh at
                  hour k (from the previous plan), so c only absorbs real PV surplus.
  Comfort margin  delta_i = quantile `margin_key` of the model's 1-6 h prediction error
                  (models/margins_<c>.json), clipped to [margin_min, margin_max].
  Forecasts       only information available at hour t:
                    reference      day-ahead, bought at day_ahead_hour (then known to the end of tomorrow),
                                   the last known day repeats beyond
                    weather        dataset forecasts issued up to t (leads 6 / 12 / 24 h)
                    plug load, DHW mean of the same hour over the last load_days days + AR(1) correction;
                                   training-month hourly profile for days before the episode start
                    PV             a_h I_dir + b_h I_dif per hour of day from the weather forecast
                    uncontrolled heating/cooling demand: same hour of the last observed day
                    hvac mode      current value, held over the horizon
  Fixed inputs    device nameplate data (heat-pump power, COP function, battery parameters) is read from
                  the environment; everything else is fitted on the training month (models/*.json).

Use: CentralMPCController.from_config(env, 'VT', 'final_vt.yaml')  (configs/final_tx.yaml, final_vt.yaml).
Requires cvxpy (participants/Joshua/requirements.txt).
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from common.evaluation import COMFORT_BOUNDS, PERIODS, make_env
from common.tuned_pi import TunedPIController

try:
    import cvxpy as cp
except ImportError as exc:  # pragma: no cover
    raise ImportError('mpc_controller needs cvxpy: pip install -r participants/Joshua/requirements.txt') from exc

HERE = Path(__file__).resolve().parent
MODEL_DIR = HERE / 'models'
CONFIG_DIR = HERE / 'configs'
WEATHER_LEADS = (6, 12, 24)   # the repo's CityLearn *_predicted_1/2/3 = leads 6/12/24 h


# ======================================================================================
# Helpers
# ======================================================================================

def _thermal_action_name(building):
    names = [n for n in building.active_actions if n in ('heating_device', 'cooling_device')]
    if 'cooling_or_heating_device' in building.active_actions:
        raise NotImplementedError('cooling_or_heating_device is not handled by this controller.')
    return names[0] if names else None


def _device_input_power(device, q, t_out, heating: bool) -> np.ndarray:
    """Electric energy needed for thermal energy q (HeatPump or ElectricHeater)."""
    q = np.asarray(q, dtype=float)
    if q.size == 0 or device is None or getattr(device, 'nominal_power', 0.0) in (None, 0.0):
        return np.zeros_like(q)
    if hasattr(device, 'get_cop'):
        return q / np.asarray(device.get_cop(t_out, heating=heating), dtype=float)
    return q / max(float(device.efficiency), 1e-6)


def _hour_features(hour: np.ndarray):
    angle = 2.0 * np.pi * np.asarray(hour, dtype=float) / 24.0
    return np.sin(angle), np.cos(angle)


def curve_value(x: float, curve) -> float:
    """Piecewise-linear lookup with CityLearn's index rule (Battery.get_current_efficiency)."""
    xs, ys = (np.asarray(v, dtype=float) for v in curve)
    j = max(0, int(np.argmax(x <= xs)) - 1)
    return float(ys[j] + (x - xs[j]) * (ys[j + 1] - ys[j]) / (xs[j + 1] - xs[j]))


def is_concave(curve) -> bool:
    xs, ys = (np.asarray(v, dtype=float) for v in curve)
    slopes = np.diff(ys) / np.diff(xs)
    return bool(np.all(np.diff(slopes) <= 1e-12))


def _load_json(name: str):
    path = MODEL_DIR / name
    if not path.exists():
        raise FileNotFoundError(f'{path} missing (fitted offline on the training month).')
    return json.loads(path.read_text())


def load_config(path) -> Dict[str, Any]:
    """YAML config: {'controller': {kwarg: value, ...}, ...}. Relative paths resolve in configs/."""
    import yaml
    path = Path(path)
    if not path.is_absolute() and not path.exists():
        path = CONFIG_DIR / path
    return yaml.safe_load(path.read_text()) or {}


# ---- Forecasts that use only data observed up to the decision step t ----

def reference_forecast(ref: np.ndarray, t: int, idx: np.ndarray, day_ahead_hour: int = 12,
                       steps_per_day: int = 24) -> np.ndarray:
    """Day-ahead reference: bought at `day_ahead_hour`. Before that hour it is known to the end of the
    current day, from that hour on to the end of the next day; beyond, the last known day repeats.
    Assumes the episode starts at midnight (true for all CE1 periods)."""
    ref = np.asarray(ref, dtype=float)
    idx = np.asarray(idx, dtype=int)
    day, hour = divmod(int(t), steps_per_day)
    known_end = steps_per_day * (day + (2 if hour >= day_ahead_hour else 1)) - 1
    back = np.ceil(np.clip(idx - known_end, 0, None) / steps_per_day).astype(int)
    return ref[np.clip(idx - steps_per_day * back, 0, len(ref) - 1)]


def issued_forecast(actual: np.ndarray, preds: Dict[int, np.ndarray], t: int, idx: np.ndarray,
                    steps_per_day: int = 24) -> np.ndarray:
    """Weather value for target indices `idx` from forecasts ISSUED at or before step t.

    preds[L][tau] is the forecast issued at tau for tau + L. For a target j = t + h the freshest usable
    forecast has the smallest lead L >= h. Beyond the longest lead the same hour of the last forecast day
    is used. h <= 0: the observed value. All arrays use the full dataset index.
    """
    actual = np.asarray(actual, dtype=float)
    idx = np.asarray(idx, dtype=int)
    leads = sorted(preds)
    longest = leads[-1]
    out = np.empty(len(idx))
    for n, j in enumerate(idx):
        h = j - t
        if h > longest:                          # e.g. h = 30 -> same hour, 6 h ahead of the +24 h issue
            j -= steps_per_day * int(np.ceil((h - longest) / steps_per_day))
            h = j - t
        if h <= 0:
            out[n] = actual[min(j, len(actual) - 1)]
            continue
        val = None
        for L in leads:
            if L >= h and 0 <= j - L < len(preds[L]):
                val = float(preds[L][j - L])
                break
        out[n] = actual[min(t, len(actual) - 1)] if val is None else val   # no issued forecast: now
    return out


def persistence_forecast(series: np.ndarray, t: int, idx: np.ndarray, fallback: np.ndarray,
                         steps_per_day: int = 24) -> np.ndarray:
    """Same hour of the most recent observed day (idx - 24 d <= t); `fallback` where that is < 0."""
    series = np.asarray(series, dtype=float)
    idx = np.asarray(idx, dtype=int)
    past = idx - steps_per_day * np.ceil((idx - t) / steps_per_day).astype(int)
    return np.where(past >= 0, series[np.clip(past, 0, None)], np.asarray(fallback, dtype=float))


def mean_days_forecast(series: np.ndarray, t: int, idx: np.ndarray, n_days: int, fallback: np.ndarray,
                       steps_per_day: int = 24) -> np.ndarray:
    """Mean of the same hour over the last `n_days` observed days; days before the episode start are
    filled with `fallback` (training-month profile)."""
    series = np.asarray(series, dtype=float)
    idx = np.asarray(idx, dtype=int)
    first = np.ceil((idx - t) / steps_per_day).astype(int)                  # most recent observed day
    past = idx[:, None] - steps_per_day * (first[:, None] + np.arange(n_days)[None, :])
    vals = np.where(past >= 0, series[np.clip(past, 0, None)], np.asarray(fallback, dtype=float)[:, None])
    return vals.mean(axis=1)


def load_forecast(series: np.ndarray, t: int, idx: np.ndarray, fallback, n_days: int = 7,
                  phi: float = 0.0) -> np.ndarray:
    """Plug/DHW load: mean of the last n_days days (fallback(tt, jj) fills days before the start)
    + AR(1) correction phi^h * (series[t] - forecast of step t made at t-1)."""
    series = np.asarray(series, dtype=float)
    idx = np.asarray(idx, dtype=int)

    def fc(tt, jj):
        return mean_days_forecast(series, tt, jj, n_days, fallback(tt, jj))

    out = fc(t, idx)
    if phi != 0.0 and t >= 1:
        err = float(series[t] - fc(t - 1, np.array([t]))[0])
        out = out + phi ** np.clip(idx - t, 0, None) * err
    return np.clip(out, 0.0, None)


def weather_arrays(weather):
    """Full-dataset weather arrays of a CityLearn Weather object (attribute access returns only the
    episode slice) -> ({key: (actual, {lead: issued})}, episode start)."""
    w = weather.__dict__
    full = {}
    for key, name in (('t_out', 'outdoor_dry_bulb_temperature'), ('i_dir', 'direct_solar_irradiance'),
                      ('i_dif', 'diffuse_solar_irradiance')):
        preds = {}
        for j, L in enumerate(WEATHER_LEADS, start=1):
            # repo CityLearn: *_predicted_1/2/3; pip release: *_predicted_6h/12h/24h
            for attr in (f'_{name}_predicted_{j}', f'_{name}_predicted_{L}h'):
                if attr in w:
                    preds[L] = np.asarray(w[attr], dtype=float)
                    break
            else:
                raise KeyError(f'weather forecast {name} lead {L} h not found')
        full[key] = (np.asarray(w[f'_{name}'], dtype=float), preds)
    return full, int(weather.start_time_step or 0)


def pv_from_irradiance(coef: Dict[str, List[float]], i_dir: np.ndarray, i_dif: np.ndarray,
                       hour: np.ndarray) -> np.ndarray:
    """PV = a_h * I_dir + b_h * I_dif per hour of day h (non-negative least squares on the training month)."""
    h = np.asarray(hour, dtype=int) % 24
    a, b = np.asarray(coef['a'], dtype=float), np.asarray(coef['b'], dtype=float)
    return np.clip(a[h] * np.asarray(i_dir, float) + b[h] * np.asarray(i_dif, float), 0.0, None)


def training_profiles(climate: str) -> Dict[str, Dict[str, List[float]]]:
    """Hour-of-day mean of plug load, DHW demand and PV per building on the TRAINING month (cached as JSON);
    fills the 7-day mean before enough days of the running episode are observed."""
    path = MODEL_DIR / f'profiles_{climate.lower()}.json'
    if path.exists():
        return json.loads(path.read_text())
    env = make_env(climate, 'train', central_agent=True)
    env.reset()
    profiles = {}
    for b in env.buildings:
        es = b.energy_simulation
        hod = np.asarray(es.hour, dtype=int) % 24
        series = dict(nsl=np.asarray(es.non_shiftable_load, dtype=float),
                      dhw=np.asarray(es.dhw_demand, dtype=float),
                      pv=np.asarray(b.pv.get_generation(es.solar_generation), dtype=float))
        profiles[b.name] = {k: [float(v[hod == h].mean()) if np.any(hod == h) else 0.0 for h in range(24)]
                            for k, v in series.items()}
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profiles, indent=2))
    return profiles


# ======================================================================================
# Controller
# ======================================================================================

class CentralMPCController(TunedPIController):
    """Centralized MPC for the whole portfolio (use with central_agent=True).

    Climate-specific settings come from configs/final_<c>.yaml: identification (thermal-model file),
    w_du, margin_max, load_ar_phi, dhw_ar_phi. The other defaults are the values of both final configs.
    Falls back to the tuned PI (battery idle) for a step if the QP cannot be solved.
    """

    def __init__(self, env, climate: str, identification: str = 'phase3', horizon: int = 24,
                 w_track: float = 100.0, w_comfort: float = 1.0, w_comfort_sq: float = 1.0,
                 w_fair: float = 10.0, w_du: float = 0.1, w_bat: float = 1e-3, w_export: float = 0.05,
                 w_term: float = 1.0, soc_target: float = 0.5, sigma_factor: float = 2.0,
                 w_sigma_sq: float = 0.0, bias_gain: float = 0.3, bias_leak: float = 0.98,
                 bias_decay: float = 0.8, bias_clip_rmse: float = 3.0, bat_eff_power_frac: float = 0.8,
                 day_ahead_hour: int = 12, margin_key: str = 'bc_q80', margin_min: float = 0.2,
                 margin_max: float = 1.0, load_days: int = 7, load_ar_phi: float = 0.0,
                 dhw_ar_phi: float = 0.0, u_max: float = 1.0, **kwargs: Any):
        super().__init__(env, **kwargs)
        self.climate = climate.upper()
        self.H = int(horizon)
        self.u_max = u_max
        self.soc_target = soc_target
        self.weights = dict(track=w_track, comfort=w_comfort, comfort_sq=w_comfort_sq, du=w_du,
                            bat=w_bat, export=w_export, term=w_term, sigma_sq=w_sigma_sq)
        self.w_fair, self.sigma_factor = float(w_fair), float(sigma_factor)
        self.bias_gain, self.bias_leak, self.bias_decay = bias_gain, float(bias_leak), float(bias_decay)
        self.bias_clip_rmse = bias_clip_rmse
        self.bat_eff_power_frac = float(bat_eff_power_frac)
        self.day_ahead_hour = int(day_ahead_hour)
        self.load_days = int(load_days)
        self.load_ar_phi, self.dhw_ar_phi = float(load_ar_phi), float(dhw_ar_phi)
        c = self.climate.lower()

        # comfort band, tightened per building by delta_i
        t_min, t_max = COMFORT_BOUNDS[PERIODS[self.climate]['season']]
        self.t_min, self.t_max = float(t_min), float(t_max)
        mdata = _load_json(f'margins_{c}.json')
        self.margin = np.clip([float(mdata[b.name][margin_key]) for b in self.buildings],
                              float(margin_min), float(margin_max))
        self.t_lo, self.t_hi = t_min + self.margin, t_max - self.margin

        # thermal models: T[k] = a1 T[k-1] + a2 T[k-2] + b0 Q[k] + b1 Q[k-1] + exogenous terms
        models = _load_json(f'thermal_{c}_{identification}.json')
        missing = [b.name for b in self.buildings if b.name not in models]
        if missing:
            raise KeyError(f'No thermal model for {missing}.')
        self.arx = [models[b.name] for b in self.buildings]
        self.rmse = np.array([float(m.get('rmse_one_step_K', 0.5)) for m in self.arx])
        self.a1 = np.array([float(m['T_prev']) for m in self.arx])
        self.a2 = np.array([float(m.get('T_prev2', 0.0)) for m in self.arx])
        self.bq1 = np.array([float(m.get('Q_prev', 0.0)) for m in self.arx])
        self._warmup = max(int(getattr(b.dynamics, 'lookback', 0)) for b in self.buildings) + 2

        prof = training_profiles(self.climate)
        self._profiles = [prof[b.name] for b in self.buildings]
        pv = _load_json(f'pv_{c}.json')
        self._pv_coef = [pv[b.name] for b in self.buildings]

        self._init_static_parameters()
        self._build_problem()
        self.reset()

    @classmethod
    def from_config(cls, env, climate: str, config, **overrides: Any) -> 'CentralMPCController':
        cfg = load_config(config) if not isinstance(config, dict) else config
        kwargs = dict(cfg.get('controller', {}) or {})
        kwargs.update(overrides)
        return cls(env, climate=climate, **kwargs)

    # ---- static data ------------------------------------------------------------------

    def _init_static_parameters(self):
        N = len(self.buildings)
        self.thermal_name = [_thermal_action_name(b) for b in self.buildings]
        self.is_heating = np.array([n == 'heating_device' for n in self.thermal_name])
        self.p_hp = np.array([
            float(getattr(b.heating_device if h else b.cooling_device, 'nominal_power', 0.0) or 0.0)
            if n else 0.0
            for b, n, h in zip(self.buildings, self.thermal_name, self.is_heating)])

        self.cap = np.zeros(N)
        self.p_bat = np.zeros(N)
        self.eta = np.ones(N)
        self.keep = np.ones(N)
        self.e_min = np.zeros(N)
        self.cap_curves = [None] * N
        for i, b in enumerate(self.buildings):
            bat = b.electrical_storage
            if 'electrical_storage' not in b.active_actions or bat is None or not bat.capacity:
                continue
            self.cap[i] = float(bat.capacity)
            self.p_bat[i] = float(bat.nominal_power)
            # efficiency at a typical operating power (CityLearn applies sqrt(eff) each way)
            self.eta[i] = float(np.sqrt(curve_value(self.bat_eff_power_frac, bat.power_efficiency_curve)))
            self.keep[i] = 1.0 - float(bat.loss_coefficient)
            self.e_min[i] = (1.0 - float(bat.depth_of_discharge)) * self.cap[i]
            self.cap_curves[i] = np.asarray(bat.capacity_power_curve, dtype=float)
        bad = [self.buildings[i].name for i, c in enumerate(self.cap_curves)
               if c is not None and not is_concave(c)]
        if bad:
            warnings.warn(f'Non-concave capacity-power curve for {bad}: only nominal power enforced there.')
            self.cap_curves = [None if c is not None and not is_concave(c) else c for c in self.cap_curves]

    # ---- QP (built once, parameters updated every step) ---------------------------------

    def _build_problem(self):
        N, H = len(self.buildings), self.H
        w = self.weights
        tile = lambda v: np.tile(np.asarray(v, dtype=float)[:, None], (1, H))
        cap_safe = np.where(self.cap > 0, self.cap, 1.0)

        P = self._par = dict(
            T0=cp.Parameter(N), E0=cp.Parameter(N, nonneg=True), u_prev=cp.Parameter(N),
            E_lo=cp.Parameter(N, nonneg=True),  # min(e_min, E0): stays feasible if SoC starts low
            G=cp.Parameter((N, H)), D=cp.Parameter((N, H)),
            umax=cp.Parameter((N, H), nonneg=True),
            base=cp.Parameter((N, H)),
            r_norm=cp.Parameter(H), inv_scale=cp.Parameter(nonneg=True),
            base_sum_norm=cp.Parameter(H),  # sum_i base_ik / scale (keeps the tracking term DPP)
            w_sigma_h=cp.Parameter(H, nonneg=True),  # export-slack price per hour, from the previous plan
            T_m1=cp.Parameter(N),      # measured T[t-1]
            G1=cp.Parameter((N, H)),   # b1 * heat-pump gain of u_{j-1} (column 0 unused)
        )
        V = self._var = dict(
            u=cp.Variable((N, H), nonneg=True),
            ch=cp.Variable((N, H), nonneg=True), dis=cp.Variable((N, H), nonneg=True),
            E=cp.Variable((N, H + 1)), T=cp.Variable((N, H + 1)),
            s=cp.Variable((N, H), nonneg=True), c=cp.Variable((N, H), nonneg=True),
        )
        u, ch, dis, E, T, s, c = (V[k] for k in ('u', 'ch', 'dis', 'E', 'T', 's', 'c'))

        p = P['base'] + cp.multiply(tile(self.p_hp), u) + ch - dis
        y = p + c
        PB = tile(self.p_bat)
        stored = cp.multiply(tile(self.eta), ch) - cp.multiply(tile(1.0 / self.eta), dis)
        # CityLearn: e_init(k+1) = (1-loss) * (e_init(k) + sqrt(eff) e_c - e_d / sqrt(eff)); E = e_init
        e_dyn = E[:, 1:] == cp.multiply(tile(self.keep), E[:, :-1] + stored)
        # T_{j+1} = a1 T_j + a2 T_{j-1} + G_j u_j + G1_j u_{j-1} + D_j   (T column j = env index t+j)
        # b1 * Q[t] (already measured) sits in D[:, 0]; T_{-1} = measured T[t-1].
        T_lag = cp.hstack([cp.reshape(P['T_m1'], (N, 1), order='F'), T[:, :-2]])
        u_lag = cp.hstack([np.zeros((N, 1)), u[:, :-1]])
        t_dyn = T[:, 1:] == (cp.multiply(tile(self.a1), T[:, :-1]) + cp.multiply(tile(self.a2), T_lag)
                             + cp.multiply(P['G'], u) + cp.multiply(P['G1'], u_lag) + P['D'])
        cons = [
            T[:, 0] == P['T0'],
            t_dyn,
            u <= P['umax'],
            E[:, 0] == P['E0'],
            e_dyn,
            E[:, 1:] >= cp.reshape(P['E_lo'], (N, 1), order='F') @ np.ones((1, H)),
            E[:, 1:] <= tile(self.cap),
            y >= 0,
            T[:, 1:] >= tile(self.t_lo) - s, T[:, 1:] <= tile(self.t_hi) + s,
            ch + dis <= PB,
        ]
        # per-building capacity-power curve, evaluated at the SoC at the start of the step (e_init / cap);
        # for a concave curve the minimum over all segment lines equals the curve -> exact
        for i, curve in enumerate(self.cap_curves):
            if curve is None:
                continue
            xs, fr = (np.asarray(v, dtype=float) for v in curve)
            soc_i = E[i, :-1] * (1.0 / float(cap_safe[i]))
            for j in range(len(xs) - 1):
                slope = float((fr[j + 1] - fr[j]) / (xs[j + 1] - xs[j]))
                limit = float(self.p_bat[i]) * (float(fr[j]) + slope * (soc_i - float(xs[j])))
                cons += [ch[i, :] + dis[i, :] <= limit]

        # district / scale, written as parameter + parameter * (parameter-free expression):
        # inv_scale * sum(y) would multiply two parameters (inv_scale * base), which is not DPP.
        district_norm = P['base_sum_norm'] + P['inv_scale'] * cp.sum(
            cp.multiply(tile(self.p_hp), u) + ch - dis + c, axis=0)
        du = cp.hstack([u[:, :1] - cp.reshape(P['u_prev'], (N, 1), order='F'), u[:, 1:] - u[:, :-1]])
        bat_idx = np.flatnonzero(self.cap > 0)
        terminal = (cp.sum_squares(cp.multiply(1.0 / cap_safe[bat_idx], E[bat_idx, H])
                                   - self.soc_target) if bat_idx.size else 0.0)
        export_cost = cp.sum(cp.sum(c, axis=0) @ P['w_sigma_h']) + w['sigma_sq'] * cp.sum_squares(c)
        cost = (w['track'] * cp.sum_squares(district_norm - P['r_norm'])
                + w['comfort'] * cp.sum(s) + w['comfort_sq'] * cp.sum_squares(s)
                + w['du'] * cp.sum_squares(du)
                + w['bat'] * cp.sum(ch + dis)
                + export_cost
                + w['term'] * terminal)
        if self.w_fair > 0:
            # fairness: worst building's horizon violation (cvxpy adds the epigraph variable)
            cost = cost + self.w_fair * cp.max(cp.sum(s, axis=1))
        self.problem = cp.Problem(cp.Minimize(cost), cons)
        if not self.problem.is_dcp(dpp=True):
            raise RuntimeError('MPC problem is not DPP; parameter updates would recompile every step.')

    # ---- per-step data ----------------------------------------------------------------

    def _rebuild_episode_cache(self):
        """Full-episode arrays that never change during the episode."""
        self._cache = []
        for b in self.buildings:
            es = b.energy_simulation
            full, w0 = weather_arrays(b.weather)
            self._cache.append(dict(
                nsl=np.asarray(es.non_shiftable_load, dtype=float),
                dhw=np.asarray(es.dhw_demand, dtype=float),
                cool_data=np.asarray(es.cooling_demand_without_control, dtype=float),
                heat_data=np.asarray(es.heating_demand_without_control, dtype=float),
                hvac=np.asarray(es.hvac_mode),
                hour=np.asarray(es.hour, dtype=float),
                # full-dataset weather: actual values are only read up to step t, forecasts up to issue time t
                w_full=full, w_start=w0,
            ))
        self._ref = np.asarray(self.buildings[0].district_load_target.district_load_target, dtype=float)
        self._episode_len = len(self._cache[0]['hour'])
        start = int(getattr(self.env.unwrapped.episode_tracker, 'episode_start_time_step', 0))
        if start % 24 != 0:
            warnings.warn(f'Episode starts at hour {start % 24}, day-ahead reference rule assumes midnight.')

    def _weather(self, d, key: str, t: int, idx: np.ndarray) -> np.ndarray:
        actual, preds = d['w_full'][key]
        return issued_forecast(actual, preds, d['w_start'] + t, d['w_start'] + idx)

    def _load(self, series, t: int, idx: np.ndarray, profile, phi: float) -> np.ndarray:
        """Plug / DHW load: mean of the last load_days days + AR(1); training profile before the start."""
        hours = self._cache[0]['hour']
        prof = np.asarray(profile, dtype=float)
        return load_forecast(series, t, idx, lambda tt, jj: prof[np.asarray(hours[jj], dtype=int) % 24],
                             self.load_days, phi)

    def _update_parameters(self, t: int, T0: np.ndarray):
        N, H = len(self.buildings), self.H
        idx = np.clip(t + 1 + np.arange(H), 0, self._episode_len - 1)  # actions act on step t+1
        G, D, umax = np.zeros((N, H)), np.zeros((N, H)), np.zeros((N, H))
        G1 = np.zeros((N, H))
        base, E0 = np.zeros((N, H)), np.zeros(N)
        bias_shape = self.bias_decay ** np.arange(H)

        for i, (b, m, d) in enumerate(zip(self.buildings, self.arx, self._cache)):
            t_out = self._weather(d, 't_out', t, idx)
            i_dir, i_dif = self._weather(d, 'i_dir', t, idx), self._weather(d, 'i_dif', t, idx)
            h_sin, h_cos = _hour_features(d['hour'][idx])
            heat = bool(self.is_heating[i])
            prof = self._profiles[i]
            hour = d['hour'][idx]

            # uncontrolled space conditioning (e.g. heating data in TX) as disturbance + load: persistence
            unc_series = d['cool_data'] if heat else d['heat_data']
            now = np.full(len(idx), float(np.asarray(unc_series, dtype=float)[min(t, len(unc_series) - 1)]))
            unc_q = persistence_forecast(unc_series, t, idx, now)
            unc_dev = b.cooling_device if heat else b.heating_device
            unc_sign = -1.0 if heat else 1.0
            if self.thermal_name[i] is None:
                unc_q, unc_sign = np.zeros(H), 0.0

            D[i] = (m['T_out'] * t_out + m['I_dir'] * i_dir + m['I_dif'] * i_dif
                    + m['hour_sin'] * h_sin + m['hour_cos'] * h_cos + m['const']
                    + m['Q'] * unc_sign * unc_q + self._bias[i] * bias_shape)
            if self.bq1[i] != 0.0:
                # b1 Q[k-1]: column 0 uses the measured Q[t] (controlled + uncontrolled), later
                # columns the uncontrolled part at idx-1 (the controlled part enters via G1 u_{j-1})
                q_now = (float(np.asarray(b.energy_from_heating_device, float)[-1])
                         - float(np.asarray(b.energy_from_cooling_device, float)[-1]))
                D[i, 0] += self.bq1[i] * q_now
                D[i, 1:] += self.bq1[i] * unc_sign * unc_q[:-1]

            if self.thermal_name[i] is not None:
                dev = b.heating_device if heat else b.cooling_device
                cop = np.asarray(dev.get_cop(t_out, heating=heat), dtype=float)
                G[i] = m['Q'] * (1.0 if heat else -1.0) * cop * self.p_hp[i]
                G1[i, 1:] = self.bq1[i] * (1.0 if heat else -1.0) * cop[:-1] * self.p_hp[i]
                hvac = np.full(H, d['hvac'][min(t, len(d['hvac']) - 1)])   # mode observed now, held
                on = np.isin(hvac, [2, 3] if heat else [1, 3])
                umax[i] = np.where(on, self.u_max, 0.0)

            dhw = self._load(d['dhw'], t, idx, prof.get('dhw'), self.dhw_ar_phi)
            dhw_el = _device_input_power(b.dhw_device, dhw, t_out, True)
            unc_el = _device_input_power(unc_dev, unc_q, t_out, not heat)
            base[i] = (self._load(d['nsl'], t, idx, prof.get('nsl'), self.load_ar_phi) + dhw_el + unc_el
                       - pv_from_irradiance(self._pv_coef[i], i_dir, i_dif, hour))

            if self.cap[i] > 0:
                E0[i] = float(b.electrical_storage.soc[b.time_step]) * self.cap[i] * self.keep[i]

        r = reference_forecast(self._ref, t, idx, self.day_ahead_hour)
        scale = max(float(np.mean(r)), 1.0)
        P = self._par
        E0 = np.clip(E0, 0.0, None)
        P['T0'].value, P['E0'].value, P['u_prev'].value = T0, E0, self._u_prev
        P['E_lo'].value = np.minimum(self.e_min, E0 * self.keep ** H)  # idle battery stays feasible
        P['G'].value, P['D'].value, P['umax'].value = G, D, umax
        P['G1'].value = G1
        P['T_m1'].value = np.array([float(np.asarray(b.indoor_dry_bulb_temperature, float)[-2])
                                    if len(b.indoor_dry_bulb_temperature) > 1
                                    else float(b.indoor_dry_bulb_temperature[-1]) for b in self.buildings])
        P['base'].value = base
        P['r_norm'].value, P['inv_scale'].value = r / scale, 1.0 / scale
        P['base_sum_norm'].value = base.sum(axis=0) / scale
        # Export-slack price: realizable district of the previous plan (sum_i max(p_ik, 0)), shifted one
        # step; cold start: uncontrolled base load. Tracking gain = 2 w_track (r_k - D_k)^+ / scale^2.
        if self._district_plan is not None:
            d_hat = np.append(self._district_plan[1:], self._district_plan[-1])
        else:
            d_hat = np.clip(base, 0.0, None).sum(axis=0)
        gain = 2.0 * self.weights['track'] * np.clip(r - d_hat, 0.0, None) / scale ** 2
        P['w_sigma_h'].value = self.sigma_factor * gain + self.weights['export']

    def _update_bias(self, T0: np.ndarray, t: int):
        """Offset-free correction: filter the one-step temperature prediction error (frozen in warm-up)."""
        if self._t_pred is None or t < self._warmup:
            return
        self._bias = self.bias_leak * self._bias + self.bias_gain * (T0 - self._t_pred)
        lim = self.bias_clip_rmse * self.rmse
        self._bias = np.clip(self._bias, -lim, lim)

    def _solve(self) -> bool:
        kwargs = dict(warm_start=True, verbose=False)
        for solver in ('OSQP', 'CLARABEL', None):
            try:
                if solver is None:
                    self.problem.solve(**kwargs)
                elif solver == 'OSQP':
                    self.problem.solve(solver=solver, eps_abs=1e-4, eps_rel=1e-4, max_iter=20000, **kwargs)
                else:
                    self.problem.solve(solver=solver, **kwargs)
            except (cp.error.SolverError, ValueError):
                continue
            if self.problem.status in ('optimal', 'optimal_inaccurate'):
                return True
        return False

    # ---- Agent interface --------------------------------------------------------------

    def reset(self):
        super().reset()
        self._plan = None
        self._t_pred = None
        self._cache = None
        n = len(self.env.unwrapped.buildings)
        self._bias = np.zeros(n)
        self._u_prev = np.zeros(n)
        self._district_plan = None
        self.n_fallbacks = 0

    def plan(self):
        """Solve the MPC for the current env state; sets self._plan (or None on failure)."""
        t = int(self.env.unwrapped.time_step)
        if self._cache is None or t == 0:
            self._rebuild_episode_cache()
        T0 = np.array([float(b.indoor_dry_bulb_temperature[-1]) for b in self.buildings])
        self._update_bias(T0, t)       # first, so the new bias enters D below
        self._update_parameters(t, T0)
        if self._solve():
            V = self._var
            self._plan = dict(u=np.clip(V['u'].value[:, 0], 0.0, 1.0),
                              bat=(V['ch'].value[:, 0] - V['dis'].value[:, 0]))
            self._t_pred = V['T'].value[:, 1]
            self._u_prev = self._plan['u']
            p = (self._par['base'].value + self.p_hp[:, None] * V['u'].value
                 + V['ch'].value - V['dis'].value)
            self._district_plan = np.clip(p, 0.0, None).sum(axis=0)
        else:
            self._plan, self._t_pred = None, None
            self._district_plan = None
            self.n_fallbacks += 1

    def thermal_action(self, i, building, action_name):
        if self._plan is None:
            return super().thermal_action(i, building, action_name)
        return float(self._plan['u'][i])

    def battery_action(self, i, building):
        if self._plan is None or self.cap[i] <= 0:
            return 0.0
        return float(self._plan['bat'][i] / self.cap[i])  # action = energy / capacity

    def predict(self, observations: List[List[float]], deterministic: bool = None) -> List[List[float]]:
        self.plan()
        return super().predict(observations, deterministic)  # maps per-building actions, clips, steps
