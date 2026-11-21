# Mehdi · Portfolio MPC

One centralized controller coordinates 25 homes over a 12-hour horizon. Each hour it forecasts district electricity demand, evaluates battery and heat-pump actions, and applies the first action. The climate selects the final policy automatically.

| Climate | Training / test month | Final policy |
|---|---|---|
| VT · heating | January / February | Adjust heating using each home’s measured temperature and recent temperature change. Use the original MPC battery dispatch to track the district target. |
| TX · cooling | June / July | Combine temperature feedback with battery scheduling by dynamic programming. Use a 25 °C planning threshold during 15:00–18:00 and provide homes approaching it with extra cooling from 13:00–18:00. |

Each run fits the load forecasts and per-home temperature models on the training month, then freezes them for testing. During the test month, decisions use current observations, forecasts and the announced district target. The shared evaluator uses the official comfort bands of 20–24 °C for Vermont and 22–26 °C for Texas.

## Results (test month)

| Climate | NMBE [%] | CV-RMSE [%] | Discomfort, median building [K·h] | Exceedance [% of hours] | Notes |
|---|---:|---:|---:|---:|---|
| VT | +40.03 | 56.60 | 83.53 | 16.40 | February · temperature-feedback heating MPC |
| TX | −3.10 | 30.94 | 32.19 | 14.05 | July · temperature-feedback MPC with per-home cooling protection and optimized battery scheduling |

NMBE measures signed energy bias. CV-RMSE measures hourly tracking error. Median discomfort is the middle home’s cumulative degree-hours outside the comfort band. Exceedance is the percentage of all building-hours outside that band. These benchmark months were inspected during development.

## Run

From the repository root, activate the repository’s CityLearn Python environment with SciPy installed. Run one command per climate.

```bash
python participants/Mehdi/run.py --climate VT
python participants/Mehdi/run.py --climate TX
```

The runner writes `summary.csv`, `daily.csv`, `buildings.csv`, and `timeseries.csv` under `results/vt_test/` or `results/tx_test/`. These local files can be regenerated with the commands above.

[Controller and battery scheduling](controller.py) · [Forecasts and model fitting](model.py) · [Experiment history](docs/experiment-history.md)
