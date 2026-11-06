# Annex 96 Common Exercise – HSLU Workspace

Shared repository for us working on the
**IEA EBC Annex 96 Common Exercise 1**.

The simulation environment, data and evaluation scripts are copied from the original exercise repository:
👉 https://github.com/kkaspar10/annex96_common_exercise_1

Each participant develops their own controller(s) in a personal folder, so we all use the
same environment and evaluation and don't get in each other's way.

---

## Repository structure

```
.
├── common/                  # ⚠️ Do not edit – see "Shared code" below
│   ├── citylearn/           # Simulation environment (copied from the original Annex repo)
│   ├── data/datasets/       # TX and VT portfolios incl. district_target.csv (copied)
│   ├── notebooks/           # Official CE1 quickstart (copied)
│   ├── evaluation.py        # Our shared evaluation: all CE1 metrics, same for everyone
│   ├── tuned_pi.py          # Baseline controller: tuned PI thermostat, battery idle
│   └── baselines/           # Pre-computed baseline runs (tuned PI, PV, battery idle)
├── participants/
│   ├── _template/           # Starting point for a new controller
│   ├── <firstname_lastname>/
│   │   ├── controller.py    # Your controller(s)
│   │   ├── run.py           # Script / notebook to run and evaluate your controller
│   │   └── README.md        # Short description of your approach and results
│   └── ...
├── requirements.txt         # Python dependencies (same as the original repo)
└── README.md
```

---

## Getting started

1. **Clone the repo**
   ```bash
   git clone <URL OF THIS REPO>
   cd <REPO NAME>
   ```

2. **Set up the Python environment**
   ```bash
   python -m venv .venv
   # Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Create your own folder**
   Copy `participants/_template/` to `participants/<firstname_lastname>/` and start working there.

4. **Run and evaluate** your controller:
   ```bash
   python participants/<firstname_lastname>/run.py --climate VT --period train   # or TX, test
   ```
   `run.py` loads the CityLearn copy from `common/` (do **not** `pip install citylearn`),
   runs the selected month and scores it with [`common/evaluation.py`](common/evaluation.py).
   Results land in `participants/<name>/results/<climate>_<period>/` (not committed).
   The exercise rules are in [`common/README.md`](common/README.md),
   the official tutorial is [`common/notebooks/CE1_quickstart.ipynb`](common/notebooks/CE1_quickstart.ipynb).

---

## Evaluation

| Climate | Training month | Test month (report these) | Comfort band |
|---------|----------------|---------------------------|--------------|
| VT (heating) | January | February | 20–24 °C |
| TX (cooling) | June    | July     | 22–26 °C |

`common/evaluation.py` computes, following the CE1 benchmark formulation:

- **Tracking:** NMBE and CV-RMSE against the official reference `district_target.csv`,
  for the whole month and per day. The district load is the sum of each building's grid import
  (PV surplus is clipped to 0, since there's no export).
- **Comfort:** cumulative discomfort per building in K·h (mean and median building),
  plus exceedance hours and the percentage of hours outside the band.
- **Secondary:** energy, peak demand and its time, daily peak-to-valley ratio, load factor and ramping,
  each compared with the baseline.
- **Per building:** energy, discomfort, and "flexibility effort" (how far the load moved from the baseline)
  as a share of the portfolio, to discuss fairness.

Cost and emissions are **not** reported because the CE1 datasets contain no price or carbon data.

**Baseline.** We compare against a **tuned PI thermostat** ([`common/tuned_pi.py`](common/tuned_pi.py):
kp = 0.02, ki = 0.005, targets 21 °C heating / 25 °C cooling, battery idle), not the official
CE1 baseline (citylearn `PITemperatureController`). The official one oscillates on the CE1 building
models (0/100 % heat pump, houses at −17…62 °C in VT January) and follows setpoints below the
comfort band, so it is no meaningful comparison. The tuned PI is also the starting point in
`participants/_template/controller.py`: unchanged, it reproduces the baseline exactly.
The **reference r_k is still the official `district_target.csv`**, so the tracking metrics
(NMBE, CV-RMSE) are the official ones. Only the `baseline_*` values and the flexibility effort
depend on our baseline. If `common/tuned_pi.py` ever changes, delete `common/baselines/*.csv`
(they are re-simulated on the next run) and tell the group.

Anyone can re-score a saved run, e.g. to compare controllers:
```bash
python common/evaluation.py participants/<name>/results/vt_test/timeseries.csv \
    --baseline-csv common/baselines/baseline_vt_test.csv
```

---

## Working together

- **Only edit files in your own folder.** That way there are no merge conflicts.
- **Shared code (`common/`, including `evaluation.py`) stays unchanged** so everyone's results are comparable.
  If something there needs fixing, open an Issue or a Pull Request and discuss it with the group first.
- **Pull before you push:** `git pull` before starting work, then commit and push your changes.
- Write a short `README.md` in your folder explaining your controller and your results.

---

## Updating from the original repo

If the original exercise repository gets updated, one person updates `common/` and tells the group.

---

## Participants

| Name | Folder | Controller approach |
|------|--------|---------------------|
|      | `participants/...` |              |

---

## Credits

Environment, data and evaluation by the IEA EBC Annex 96 Common Exercise team –
see the [original repository](https://github.com/kkaspar10/annex96_common_exercise_1)
and its license.
