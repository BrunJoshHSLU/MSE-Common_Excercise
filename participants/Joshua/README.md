# Joshua – Central MPC

## Approach
One **centralised model predictive controller** for all 25 buildings (`mpc_controller.py`, `CentralMPCController`).

**Each hour, the controller:**
- solves one convex quadratic program (CVXPY, OSQP) over the next 24 h;
- applies only the first hour;
- repeats the next hour with new measurements (receding horizon).

**What it controls:**
- the heat-pump power fraction of every building;
- every building's battery charge/discharge.

**Objective:** keep the district grid import close to the day-ahead reference while every building stays in the comfort band.

The main parts:

- **Objective:** squared tracking error of the district import, plus
  - soft comfort violations (linear + quadratic),
  - a worst-building fairness term,
  - heat-pump smoothness,
  - battery throughput,
  - a terminal SoC target.
- **Thermal model:** one linear ARX model per building (1st or 2nd order).
  - It is fitted by bounded least squares on excitation runs of the training month.
  - It is corrected online by a bounded, decaying bias filter.
  - The heat-pump COP and nameplate power come from the environment.
- **Battery:** CityLearn's storage equation per battery, with:
  - loss,
  - efficiency at 80 % power,
  - the capacity-power curve as linear constraints.
- **No export:** the KPI clips every building's import at 0.
  - The QP models this with a slack whose price per hour is set above its possible tracking gain.
  - So the slack only absorbs real PV surplus.
- **Comfort:** the band is tightened per building by a margin (80 % quantile of the model's 1–6 h prediction error).
  - The cap is 1.0 K in TX and 0.75 K in VT.
  - Violations are soft and penalised.
- **Information (no future data):**
  - day-ahead reference (bought at 12:00);
  - issued weather forecasts (6/12/24 h);
  - 7-day-mean + AR(1) plug/DHW forecast;
  - PV from an irradiance model;
  - persistence for uncontrolled demand;
  - current hvac mode.
- **Fallback:** if the QP cannot be solved, the tuned PI runs for that hour with the batteries idle. This never happened in the final runs.

All fitting uses the training month only. The weights were chosen on the training month with a rule fixed in advance:
- the rule: lowest CV-RMSE with median discomfort ≤ 5 K·h (TX) / ≤ 15 K·h (VT);
- result: TX `w_du = 1.0`; VT margin cap 0.75 K.

The test month was then run once.

## Folder contents
| Path | Content |
|------|---------|
| `mpc_controller.py` | the controller |
| `run.py` | runs one month and evaluates it with `common/evaluation.py` |
| `controller.py`, `plots.py` | template PI controller and figures (both used by `run.py`) |
| `configs/final_tx.yaml`, `configs/final_vt.yaml` | final settings per climate |
| `models/` | identified thermal models, comfort margins, PV models, training-month load profiles (all from the training month) |
| `results/` | output of the four final runs |
| `requirements.txt` | extra dependencies: `cvxpy`, `scipy`, `pyyaml`, `pytest`, `matplotlib` |

## How to run
```bash
pip install -r participants/Joshua/requirements.txt
python participants/Joshua/run.py --climate VT --period test --controller mpc --config final_vt.yaml
python participants/Joshua/run.py --climate TX --period test --controller mpc --config final_tx.yaml
```

- **Arguments:**
  - Use `--period train` for the training month.
  - Without `--controller mpc`, `run.py` runs the template PI controller.
- **Output:** `results/<climate>_<period>_mpc_final_<climate>/` (`summary.csv`, daily and per-building CSVs, time series, figures).
- **Runtime:** about 1 s per hourly step, so 15–30 min per month.

## Results (test month)
| Climate | NMBE [%] | CV-RMSE [%] | Discomfort, median building [K·h] | Exceedance [% of hours] | Notes |
|---------|----------|-------------|-----------------------------------|-------------------------|-------|
| VT      | 33.3     | 43.9        | 15.1                              | 4.8                     | Tuned PI: 44.9 / 70.7 / 171. 12.6 of the 15.1 K·h fall in the 15 h LSTM warm-up. NMBE is set by the reference: comfort needs ~27 % more energy than the reference on every training day. |
| TX      | 3.9      | 16.8        | 1.95                              | 0.87                    | Tuned PI: 19.0 / 84.6 / 197. 0 K·h after the warm-up. Remaining error is mostly the plug/DHW load forecast (3.3 % CV-RMSE with perfect information). |

Training month for comparison:

| Climate | NMBE [%] | CV-RMSE [%] | Median discomfort [K·h] |
|---------|----------|-------------|-------------------------|
| VT      | 32.4     | 39.6        | 12.4                    |
| TX      | 2.0      | 19.2        | 2.3                     |

TX uses June/July, as set in `common/evaluation.py`.
