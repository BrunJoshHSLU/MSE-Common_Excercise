# Experiment history

This record summarizes the development experiments on the 2018 portfolios. The [participant README](../README.md) gives the final controllers, benchmark results and run commands.

## 2026-11-20 · Benchmark setup

- Reviewed the HSLU fork and created a participant workspace from the shared template. Implementation and experiment files live under `participants/Mehdi/`.
- Adopted the HSLU runner’s periods. Vermont uses January for training and February for evaluation. Texas uses June and July. The upstream exercise text also mentions August and September for Texas, so comparisons within this group follow the actual runner.
- Confirmed that each portfolio has 25 homes, PV, batteries and seasonal heat pumps. The district target is provided in advance. The evaluator sums positive grid import per building. Vermont’s comfort band is 20–24 °C and Texas’s is 22–26 °C.
- Chose the shared tuned PI thermostat with idle batteries as the baseline. Held the simulator, target, weather, portfolios and evaluator fixed across controllers.

## 2026-11-20 · First controller comparison (v1)

- Ran the baseline thermostat during each training month to collect building observations. Fitted a temperature response for each home and an hourly forecast for net non-HVAC demand, heat-pump power and outdoor temperature. Both climates selected a forecast decay of 0.95 on the final seven training days. Refit the hourly profiles on the complete training month.
- Implemented battery-only MPC, then joint battery and heat-pump MPC. Both use a 12-hour horizon. The battery variant retains the baseline thermostat. The joint variant tests a common heat-pump offset with a per-home safeguard. CityLearn applies the first command to the next hour.
- Evaluated all three policies in both benchmark months. Reviewed the time series, daily metrics, per-home metrics and summaries. Recomputed the four headline metrics from all six saved time series. Checked battery allocation bounds and the forecast across midnight. Battery-only and baseline temperatures agreed to within 0.00002 °C.
- Joint MPC had the lowest CV-RMSE in both benchmark months. Vermont still exceeded the daily energy target on 27 of 28 February days. In July, Texas had a monthly NMBE of −0.90% with CV-RMSE of 34.27%. Almost cancelling the monthly error still left substantial hourly deviations.
- Recorded v1 against shared repository commit `199ac03`. Git blob IDs identify the participant source used for these results: `controller.py` `36254e17042415b8ec32091fa961b970ad91c4b5`, `model.py` `c333a0623180e71f12df2d16497f91599c20eabc`, and `run.py` `79e06c0cce921536149f11a04011ac786fca1b30`. These identify file contents prior to the participant’s first commit.

## 2026-11-20 · Training-month energy and comfort diagnostics

**Question.** How much of the district mismatch comes from total energy over the month, and when do heat-pump load, battery charge and indoor discomfort coincide?

Replayed v1 joint MPC on January and June using each climate’s fitted model. This is an in-sample description of operation. The February and July benchmark runs remain the comparison for v1. The figures use the final seven calendar days in both climates, chosen before plotting. The hourly and daily tables cover every day of each month.

- The first panel compares the announced target with the shared definition of grid import. The second shows heat-pump electricity, other demand (domestic hot water and non-shiftable demand), and gross solar generation. Solar is plotted as a positive supply.
- The third panel shows stored energy as a percentage of total nominal battery capacity. The last panel separates homes below the lower bound from homes above the upper bound. Each home represents four percentage points at a given hour.
- The daily tables include charge and discharge separately, the unused per-home surplus, and opening and closing stored energy. Opening energy uses the preceding interval’s closing value and the reset state on day one.
- The script checks the energy balance for each house and hour, and compares reconstructed district import with the shared evaluator.


### Whole-month findings

| v1 joint MPC | Vermont, January | Texas, June |
|---|---:|---:|
| Grid import (MWh) | 100.96 | 16.96 |
| Target energy (MWh) | 74.07 | 17.13 |
| Heat-pump electricity (MWh) | 94.37 | 17.08 |
| Other electricity demand (MWh) | 21.23 | 17.31 |
| Gross solar generation (MWh) | 15.62 | 20.95 |
| Days with energy above target | 31 of 31 | 15 of 30 |
| Building-hours below band | 21.73% | 7.89% |
| Building-hours above band | 3.55% | 15.67% |

**Vermont.** Even granting lossless storage, full use of every solar kWh, and use of all initially stored energy, battery rescheduling alone leaves a monthly gap of at least **25.90 MWh** with the observed building demand. This lower bound equals heat-pump electricity + other demand − gross solar generation − initial stored energy − target energy. It applies to this fixed demand trajectory. Changing heat-pump operation could change the bound. With homes already cold for 21.73% of building-hours, the next investigation should examine how much heat each home receives and how well the temperature model predicts its response.

**Texas.** Monthly import was only **0.17 MWh below target**, yet June contained 15 days above target and homes were too hot for 15.67% of building-hours. In the displayed week, 21 of 25 homes simultaneously exceeded 26 °C at the worst hour. The chart motivates testing afternoon temperature forecasts and the timing of cooling. It does not establish that earlier cooling would improve the results.

**Verification.** Checked all 744 January hours and 720 June hours. Reconstructed each day's import from heat pumps, other demand, solar, battery flow and unused surplus. Checked that every day's closing battery energy carried into the next day. Inspected both rendered figures.

## Planned work

| Order | Status | Experiment and decision |
|---|---|---|
| 1 | Completed below | Audit 1-, 6-, and 12-hour forecasts on chronological validation blocks inside January and June. Fit each model strictly before its validation block. Record district load error and per-home temperature error, including cold Vermont mornings and hot Texas afternoons. Check missed comfort violations. |
| 2 | Trend and delivered-energy comparisons complete (Nov 21) | Use the forecast audit to choose one change. If the model misses thermal response, improve that predictor first. Otherwise compare home-specific heat-pump adjustments with v1's common offset. Evaluate each change separately against v1 on identical blocks. |
| 3 | Per-home trial complete; targeted experiments proposed below | For Texas, test whether targeted pre-cooling reduces hot building-hours and hourly tracking error. For Vermont, quantify the tracking–comfort trade-off and identify homes with persistent cold exposure. Record CV-RMSE, NMBE, discomfort and the distribution across homes. |
| 4 | After validation | Freeze the selected settings, rerun the shared February and July comparisons, and record improvements and regressions. These months have already been inspected. A fresh period for an independent assessment requires group agreement. |

## 2026-11-20 · Chronological forecast audit

**Question.** How accurately can the fitted model anticipate district import and individual comfort violations before the optimizer chooses a schedule?

Replayed the shared PI thermostat with idle batteries in January and June. For each climate, fitted two expanding training prefixes. Vermont validation blocks were Jan 18–24 and Jan 25–31; Texas blocks were Jun 17–23 and Jun 24–30. Each fit used only measurements through the day before the block, including its internal forecast-decay selection. Decays were 0.95/0.95 for Vermont and 0.80/0.95 for Texas. This audit evaluates the underlying predictor on thermostat operation. A change in MPC behavior still requires a closed-loop comparison.

Scored identical target hours for all leads, omitting the first 11 target hours of each block so even the earliest 12-hour forecast could use the fitted model. Each climate contributed 314 target hours and 7,850 home-hour predictions per horizon. Forecasts recursively used predicted outdoor temperature and predicted heat-pump electricity. District import was clipped at zero per home. A missed violation means the actual temperature was outside the band when the prediction was inside. The denominator is all actual violations. MAE means mean absolute error.

| Portfolio | Lead | District import MAE (kW) | Indoor MAE (°C) | Missed / actual violations | Miss rate |
|---|---:|---:|---:|---:|---:|
| VT | 1 h | 11.30 | 0.20 | 92 / 1,345 | 6.84% |
| VT | 6 h | 27.87 | 0.73 | 379 / 1,345 | 28.18% |
| VT | 12 h | 30.14 | 1.06 | 629 / 1,345 | 46.77% |
| TX | 1 h | 3.81 | 0.17 | 78 / 2,120 | 3.68% |
| TX | 6 h | 5.86 | 0.63 | 318 / 2,120 | 15.00% |
| TX | 12 h | 6.00 | 0.86 | 754 / 2,120 | 35.57% |

**Weather windows.** Selected the coldest quartile of Vermont morning target hours (05–09) and the hottest quartile of Texas afternoon target hours (13–18) within each validation block. These labels use realized weather only for retrospective scoring. A trial using calibration-period weather thresholds yielded no qualifying Vermont morning hours, so these results refer to relative extremes within each validation week. At 12 hours, Vermont's predictions averaged 0.73 °C too warm and missed 206 of 307 violations across 20 qualifying hours. Texas predictions averaged 0.85 °C too cool and missed 196 of 445 violations across 26 hours. The largest whole-block 12-hour MAE among individual homes was 1.45 °C for VT building 525859 and 1.29 °C for TX building 471221.

**Attribution check.** Reran the temperature equation offline with realized heat-pump power and outdoor temperature over each forecast horizon. Twelve-hour MAE fell from 1.06 to 1.02 °C in Vermont and from 0.86 to 0.68 °C in Texas. For comparison, carrying the current indoor temperature forward gave 1.20 °C and 2.37 °C respectively. The offline calculation is a diagnostic of the thermal equation under known inputs. It is excluded from the deployable forecasts.

**Decision.** Next compare v1 with a per-home thermal predictor that also uses the most recent indoor temperature change. Run both on these same folds and inspect 1-, 6- and 12-hour error, missed violations and false alarms. Only advance a candidate to a controller trial if the comfort forecast improves without a material regression at short leads. Keep the per-home dispatch and pre-cooling experiments pending until that comparison.

**Verification.** Checked that per-home counts and MAEs reconcile with the portfolio tables for both windows and all three horizons. Extracted the prefix-fitting routine in `model.py`; refitting the full months reproduced the saved v1 profiles, thermal parameters and decay within numerical tolerance of 1e−9.

## 2026-11-21 · Does the latest temperature change improve prediction?

**Method.** Refit v1's per-home temperature equation with one extra input, the indoor temperature change over the preceding hour. Limited its signed coefficient to [−0.9, 0.9] to bound the contribution of that term. The first prediction uses two measured temperatures. Later steps use the candidate's own predicted temperatures. Used identical training samples, exogenous forecasts, validation folds, and target hours for v1 and the candidate.

**Decision rule set before scoring.** For each climate, require lower mean absolute error (MAE) and lower balanced comfort-classification error at both 6 and 12 hours. Allow at most a 0.05 °C increase in 1-hour MAE. Balanced error is the average of the missed-violation rate (among actually uncomfortable home-hours) and the false-alarm rate (among actually comfortable home-hours). These criteria are experimental choices.

| Climate | Lead | MAE, v1 → trend (°C) | Miss rate, v1 → trend (%) | False-alarm rate, v1 → trend (%) | Balanced error, v1 → trend (%) |
|---|---:|---:|---:|---:|---:|
| Vermont | 1 h | 0.20 → 0.18 | 6.84 → 6.77 | 4.29 → 3.44 | 5.56 → 5.10 |
| Vermont | 6 h | 0.73 → 0.70 | 28.18 → 26.17 | 17.86 → 17.19 | 23.02 → 21.68 |
| Vermont | 12 h | 1.06 → 1.07 | 46.77 → 44.76 | 26.01 → 26.46 | 36.39 → 35.61 |
| Texas | 1 h | 0.17 → 0.16 | 3.68 → 3.68 | 4.75 → 4.52 | 4.21 → 4.10 |
| Texas | 6 h | 0.63 → 0.65 | 15.00 → 14.72 | 16.44 → 17.42 | 15.72 → 16.07 |
| Texas | 12 h | 0.86 → 0.89 | 35.57 → 35.66 | 16.11 → 17.40 | 25.84 → 26.53 |

**Decision.** Retain v1 for both portfolios. Vermont improved at six hours and caught 27 additional violations at twelve hours, with 29 additional false alarms. Its twelve-hour MAE increased slightly. Texas improved at one hour and lost accuracy at both longer horizons. Neither candidate qualified for a closed-loop trial. These observations cover two validation blocks on the thermostat trajectory, and the small error differences are descriptive.

**Checks.** Checked identical fold definitions, target counts, actual violation counts, and district load errors across variants. Summed the 25 per-building missed violations and false alarms and verified each portfolio rate for every horizon and weather subset.

**Next proposed experiment.** Test whether delivered heating or cooling energy is a better thermal input than heat-pump electricity. Heat output per electrical kWh can vary with outdoor conditions. First verify what equipment efficiency information is available at decision time. Compare the new input against v1 on the same folds with the same decision rule. Reserve February and July for controller benchmarking.

## 2026-11-21 · Delivered heating and cooling versus electrical input

**Question and implementation.** Does converting heat-pump electricity into thermal output improve indoor-temperature predictions? Each initialized CityLearn heat pump exposes its coefficient-of-performance function, technical efficiency, and supply-temperature settings at decision time. For calibration hours, we multiplied observed electrical input by the pump's COP at the observed outdoor temperature. For every forecast step, we used predicted electricity and the COP at the **forecast** outdoor temperature. Only the thermal equation received the converted input. The exogenous forecasts, district electricity calculation, fitting constraints, chronological splits, and score definitions match v1.

**Verification.** Compared the conversion with simulated heating or cooling demand for each of the 25 homes over 48 consecutive steps per climate. The largest discrepancy was under 0.000002 kWh per home-hour. Checked matching fold definitions, identical district load errors and sample counts, and reconciliation of missed violations and false alarms with the per-home reports.

| Climate | Lead | Indoor MAE, v1 → delivered (°C) | Miss rate, v1 → delivered (%) | False-alarm rate, v1 → delivered (%) | Balanced error, v1 → delivered (%) |
|---|---:|---:|---:|---:|---:|
| Vermont | 1 h | 0.20 → 0.19 | 6.84 → 7.43 | 4.29 → 3.90 | 5.56 → 5.67 |
| Vermont | 6 h | 0.73 → 0.75 | 28.18 → 31.15 | 17.86 → 17.51 | 23.02 → 24.33 |
| Vermont | 12 h | 1.06 → 1.13 | 46.77 → 49.14 | 26.01 → 25.33 | 36.39 → 37.24 |
| Texas | 1 h | 0.17 → 0.20 | 3.68 → 3.11 | 4.75 → 6.20 | 4.21 → 4.65 |
| Texas | 6 h | 0.63 → 0.88 | 15.00 → 17.22 | 16.44 → 24.33 | 15.72 → 20.77 |
| Texas | 12 h | 0.86 → 1.25 | 35.57 → 47.17 | 16.11 → 26.06 | 25.84 → 36.61 |

**Decision.** Retain v1 in both climates. Both 6-hour and 12-hour errors increased on the same 314 validation hours per climate. Texas showed the largest regression. At 12 hours the candidate missed 1,000 of 2,120 comfort violations, compared with 754 for v1. The results concern this particular fitted equation and thermostat trajectory. Neither candidate qualified for a controller trial under the previously recorded rule.


**Next proposed experiment.** Keep v1’s temperature equation fixed and compare the current common heat-pump offset with per-home offsets based on measured comfort margin. Evaluate tracking, discomfort and the distribution of discomfort across homes on the training-month validation blocks before revisiting February or July.

## 2026-11-21 · Paired controller trial with per-home heat-pump adjustments

**Question.** Can allocating the same candidate heat-pump adjustment according to each home's comfort margin reduce discomfort without sacrificing district tracking? Texas was launched first, followed by Vermont. Both use the v1 predictor, 12-hour horizon, action candidates, objective weights and battery allocation.

**Allocation.** In each of the first three planned hours the candidate starts with v1's feasible heat-pump adjustments. Homes on opposite sides of the comfort-band midpoint can exchange part of those adjustments in proportion to their distance from the midpoint. For example, in Texas, a warm home can give up some of a proposed cooling reduction and a cooler home can absorb it. A recipient can take up to an extra four percent of its pump rating, subject to available power. Each transfer preserves the district's requested heat-pump kW for that candidate at that simulated state. The optimizer can then choose a different candidate and battery command. At decision time the allocation uses measured indoor temperatures; later planning steps use predicted temperatures.

**Protocol.** Used June 17–23 and 24–30 for Texas, and January 18–24 and 25–31 for Vermont. Refit the unmodified v1 predictor using only data before each block. Each arm started with an identical one-day PI warm-up, excluded from scoring, and was simulated in closed loop for 168 scored hours. The two-week pooled score combines the weekly trajectories; state resets between weeks. The worst-five measure ranks homes by cumulative discomfort in K·h and averages the five highest. The report also follows the same five homes identified in the v1 run.

**Checks.** In 400 randomized heating and cooling allocation cases, checked conservation of candidate heat-pump power, per-device limits, and protection of homes already outside the relevant comfort bound. The trial runner checks identical observed loads and temperatures during paired warm-ups.

| Two validation weeks pooled | Controller | CV-RMSE (%) | Building-hours outside band (%) | Mean discomfort of worst five (K·h/home) |
|---|---|---:|---:|---:|
| Texas | v1 joint | 34.11 | 22.26 | 126.23 |
| Texas | Per-home | 34.25 | 21.95 | 124.20 |
| Vermont | v1 joint | 51.48 | 20.26 | 122.03 |
| Vermont | Per-home | 50.63 | 23.60 | 162.91 |

**Texas.** In the first week, CV-RMSE rose from 34.36% to 34.76%; in the second it fell from 33.85% to 33.74%. Comfort improved slightly in both weeks. Across 8,400 home-hours the candidate avoided 26 violations (23 fewer below 22 °C and three fewer above 26 °C). Most of the gain came from reducing overcooling. The afternoon overheating problem remains.

**Vermont.** Tracking improved and comfort worsened in each week. The two-week increase of 280 out-of-band home-hours comprised 166 additional cold and 114 additional hot hours. The same five homes ranked worst in each arm in the pooled comparison for each climate. Their mean Vermont discomfort rose by 40.88 K·h per home.

**Decision.** Keep v1 as the benchmark in both climates. Texas showed a small trade-off, and Vermont's tracking gain came with a sizeable comfort loss. Neither portfolio met the stated goal of improved comfort with equal or better pooled tracking. These are training-month validation results; February and July retain their existing v1 benchmark scores. Re-scored all twelve saved weekly/pooled policy tables from their time series and checked the tracking, exceedance and worst-five metrics.

**Next proposed experiment.** For Texas, test a narrower rule that specifically protects warm homes from cooling reductions when predicted to cross 26 °C. Score hot and cold hours separately as well as tracking. For Vermont, first inspect per-home temperature and heat commands around the new violations before choosing another intervention.

## 2026-11-21 · Texas forecast-based cooling safeguard

**Question.** Can the controller reduce hours above 26 °C by acting before a predicted crossing? Compare hot and cold home-hours separately, alongside CV-RMSE and mean cumulative discomfort of the five worst-affected homes.

**Candidate.** At each decision, use v1's per-home thermal model to forecast up to three hours of nominal cooling. Flag homes predicted to exceed 26 °C. Retain all 18 original MPC plans and add 18 alternatives with targeted cooling. In a guarded plan's first three hours, a flagged home above the 22 °C lower bound receives at least nominal cooling plus four percentage points of its rated pump power, subject to its power limit. Other homes keep that candidate's standard commands. Battery dispatch accounts for the revised demand. The original 12-hour tracking and comfort score chooses among all plans.

**Protocol.** Fit exclusively on earlier training-month observations and use the existing paired June 17–23 and June 24–30 closed-loop trial. Keep the same PI warm-up, thermostat, battery limits, model and objective as v1. The three-hour lookahead and four-percentage-point adjustment were fixed before the trial.

| Texas validation block | Controller | Hours above 26 °C across homes | Hours below 22 °C across homes | CV-RMSE (%) | Worst-five mean (K·h per home) |
|---|---|---:|---:|---:|---:|
| June 17–23 | v1 | 609 | 200 | 34.36 | 55.57 |
| June 17–23 | Safeguard | 587 | 207 | 34.47 | 56.14 |
| June 24–30 | v1 | 775 | 286 | 33.85 | 71.26 |
| June 24–30 | Safeguard | 779 | 293 | 34.23 | 72.19 |
| Pooled | v1 | 1,384 | 486 | 34.11 | 126.23 |
| Pooled | Safeguard | 1,366 | 500 | 34.35 | 126.86 |

**Decision.** Retain v1. The candidate avoided 22 hot home-hours in week one but added four in week two. Across 8,400 home-hours, it avoided 18 hot hours and added 14 cold hours. Tracking error and worst-five discomfort increased in both weeks. These results give little reason to promote the safeguard to the July benchmark. If pursuing targeted cooling further, first record predicted crossings, guarded-plan selections, and first-step cooling actually applied. That would help distinguish a forecast that misses the event from a plan the optimizer declines.

**Verification.** The rerun reproduced the saved v1 load and temperature trajectories from the earlier dispatch trial. Recounted hot/cold hours and recomputed tracking error and worst-five discomfort from the six weekly and pooled time series. Checked the crossing detector against a synthetic three-step forecast, a truncated horizon and unmodified input temperatures.

## 2026-11-21 · Where Texas cooling warnings translated into action

Replayed both arms on June 17–23 and 24–30 with decision tracing. At issue hour t, recorded each home's measured temperature, nominal three-hour overheating warning, winning portfolio plan and first cooling command. At t+1, recorded applied electrical cooling power and resulting indoor temperature. Verified that all four load and temperature trajectories matched their saved trial outputs within 0.00002 in their respective units. Applied power matched the safeguard's request within 0.000001 kW. The log covers 8,400 unique home-hours.

A direct boost compares a guarded plan with its paired unguarded plan at the same state and battery/heat-pump policy settings. A stricter comparison uses the highest-scoring ordinary v1 alternative available at that **same** state (lowest objective value). The separately simulated v1 arm gives the overall paired outcome, including earlier differences in temperature and battery charge.

| Measure | June 17–23 | June 24–30 |
|---|---:|---:|
| Home-hour warnings (including already-hot homes) | 944 | 1,029 |
| Warned home-hours with a direct boost | 406 | 397 |
| Direct boosts exceeding the best ordinary command | 84 | 76 |
| Median direct boost over the paired plan (kW per home) | 0.119 | 0.119 |
| New overheating episodes included in lead-time analysis | 135 | 169 |
| Episodes with warning before crossing | 107 | 130 |
| Episodes with a direct boost before crossing | 38 | 32 |
| Episodes with a warned command above best ordinary cooling before crossing | 6 | 0 |
| Hours hot only under v1 / only under the safeguard | 43 / 21 | 33 / 37 |
| Hours cold only under v1 / only under the safeguard | 2 / 9 | 2 / 9 |

An onset is a transition from ≤26 °C to >26 °C in the safeguard run. Exclude the first two application hours per block from onset scoring and look back up to three decisions made during the immediately preceding in-band-or-colder period. In week two, 125 of 130 warned episodes had their first warning at least two hours ahead. Across both weeks, 42 of the 70 directly boosted episodes first received that boost only one hour before the crossing. Repeated warnings are correlated. Episodes avoided by the safeguard are absent from these onset counts.

**Finding.** During week two, 321 of 397 direct boosts merely matched the cooling that the best ordinary plan would already have requested for that home from the same state. Guarded options can undo a common cooling reduction and still deliver the same first command as an ordinary plan. Among the 169 observed crossings, 39 had no advance warning; the other 130 had warnings but none received more cooling than the best ordinary alternative while warned before crossing. The paired trajectories show 33 home-hours hot only under v1 and 37 only under the safeguard. An individual hourly comparison cannot isolate the effect of a single command.

**Decision and next test.** Keep v1. Test one candidate defined relative to the best ordinary first command, giving warned homes a genuinely incremental, bounded early cooling option. Compare actual extra kW, cold hours, hot hours, tracking and worst-five discomfort on the same blocks. Larger boosts have not been validated.


## 2026-11-21 · Four alternative MPC strategies

**Protocol.** Compare all four alternatives with joint MPC v1 on the same two January/June validation weeks per climate. All arms share the 24-hour PI warm-up, hardware, target, forecast model and comfort bands. Each block's model is fitted before its validation week. Use the shared evaluator on the two weeks pooled. Score out-of-band hours and cumulative discomfort separately, including the five worst homes. A monthly finalist for tracking minimizes validation CV-RMSE among challengers. A second finalist minimizes the arithmetic mean of CV-RMSE and out-of-band percentage, each divided by v1's value for that climate. This gives proportional tracking and comfort changes equal weight. Break ties using discomfort among the worst five. The rule can select the same challenger twice; benchmark each distinct pick once alongside v1. These criteria were fixed before inspecting the monthly results. V1 stays eligible as the final recommendation.

| Variant | What changes |
|---|---|
| Increment above v1 | Forecast up to three hours to identify hot Texas homes or cold Vermont homes. First rank all ordinary v1 plans. Compare the winning plan against its own variant that requests up to four additional percentage points of pump rating in at-risk homes during the first three hours. The same objective chooses between the two. |
| Storage DP | Keep v1's heat-pump decision. Take its forecast of pre-storage demand and schedule the aggregate usable battery charge over 12 hours by dynamic programming. Minimize squared district tracking error, accounting for behind-meter PV surplus, aggregate power, storage efficiency and standby losses. The state grid uses 101 evenly spaced energy levels plus the exact initial level. Allocate the first grid correction to individual homes using v1's local power, charge and no-export constraints. The aggregate schedule approximates individual charge availability and nonlinear device efficiencies. |
| Thermal feedback | Replace the first nominal PI command with a blend of PI and a measured-state correction. The correction starts from the previous applied electrical power, steers toward the comfort-band midpoint and damps the most recent measured temperature change. Bound the adjustment at 10% of rating before the 50/50 blend. V1's 12-hour candidate planner and forecast remain in use. This changes dispatch directly; the earlier temperature-trend experiment changed the forecast equation. |
| Feedback + storage DP | Combine measured-state thermal feedback with the aggregate battery scheduler to test whether their effects complement each other. |


### Pooled training-week results

| Climate | Strategy | CV-RMSE (%) | Outside band (%) | Worst-five mean discomfort (K·h/home) |
|---|---|---:|---:|---:|
| Texas | v1 | 34.11 | 22.26 | 126.23 |
| Texas | Increment above v1 | 34.19 | 19.82 | 110.93 |
| Texas | Storage DP | 35.80 | 22.27 | 126.62 |
| Texas | Thermal feedback | 33.30 | 13.29 | 76.47 |
| Texas | Feedback + storage DP | 32.94 | 13.29 | 76.51 |
| Vermont | v1 | 51.48 | 20.26 | 122.03 |
| Vermont | Increment above v1 | 44.10 | 28.51 | 275.42 |
| Vermont | Storage DP | 52.58 | 24.46 | 137.81 |
| Vermont | Thermal feedback | 46.96 | 21.99 | 125.30 |
| Vermont | Feedback + storage DP | 47.84 | 24.46 | 138.91 |

The Texas combined policy won both selection criteria. Most of its comfort improvement was already present with thermal feedback alone. Vermont's incremental protection won on tracking and had the highest discomfort in the five worst homes. Thermal feedback won the balanced comparison among challengers, with a normalized score of 0.999 versus 1.000 for v1. That near tie motivated testing both Vermont finalists. Verified the two weekly and pooled reports for all five strategies per climate against their saved time series and matched v1 to its earlier paired trial. In 35 additional two-step storage examples, dynamic programming chose the same first grid import as exhaustive enumeration.

### Full-month benchmarks

| Climate | Controller | CV-RMSE (%) | NMBE (%) | Outside band (%) | Median discomfort (K·h/home) | Worst-five mean (K·h/home) |
|---|---|---:|---:|---:|---:|---:|
| Texas, July | Shared baseline | 84.62 | 19.04 | 32.18 | 197.07 | 450.52 |
| Texas, July | v1 joint MPC | 34.27 | −0.90 | 26.55 | 129.66 | 325.14 |
| Texas, July | Feedback + storage DP | **31.93** | −3.14 | **18.08** | **70.47** | **191.27** |
| Vermont, February | Shared baseline | 70.73 | 44.91 | 23.45 | 170.48 | 259.17 |
| Vermont, February | v1 joint MPC | 59.22 | 42.81 | 20.58 | 135.39 | 219.40 |
| Vermont, February | Increment above v1 | 58.30 | 41.78 | 19.43 | 118.98 | 208.14 |
| Vermont, February | Thermal feedback | **56.60** | **40.03** | **16.40** | **83.53** | **172.00** |

**Texas.** Use feedback + storage DP as the leading policy for hourly tracking and comfort. It avoided 1,079 hot and 496 cold home-hours versus v1, reduced discomfort in all 25 homes, and improved daily CV-RMSE on 24 of 31 days. Monthly bias moved from −0.90% to −3.14%, a regression in total energy matching. The two thermal-feedback variants produced almost identical validation comfort; only the combined policy was selected for July, so the individual contribution of storage scheduling to July's gains was not measured.

**Vermont.** Use thermal feedback as the leading February policy. It avoided 634 cold and 68 hot home-hours, reduced cumulative discomfort in 24 of 25 homes and improved daily CV-RMSE on 20 of 28 days. Mean discomfort among the worst five fell from 219.40 to 172.00 K·h per home. The district still imported 40.03% more energy than the target. Increment above v1 had ranked first for tracking during the two January weeks; thermal feedback performed better during February. This change in ranking limits conclusions about other months.

**Checks and next evaluation.** Verified 30 weekly/pooled validation reports and seven monthly reports against the saved time series, including per-home discomfort and hot/cold hour counts on the monthly runs. The rerun reproduced v1's previous monthly trajectories. Record these two climate-specific finalists as the current results for group comparison. A new, group-agreed seasonal period would give an independent check of the improvements.

## 2026-11-21 · Reduce discomfort in the median home

**Training diagnostic.** For each existing climate winner, ranked all 25 homes by cumulative discomfort over the two validation weeks. Among ranks 10–16, 81.0% of the Texas hot degree-hours occurred at 15:00–18:00 inclusive. The Vermont window 02:00–05:00 captured 32.8% of cold degree-hours in these homes. Cold exposure also peaked at neighboring hours. The windows originated from inspecting July/February. Their recurrence on training weeks supports testing them, and limits claims of independent confirmation.

**Trial fixed before the monthly comparison.** Use Texas feedback + storage DP and Vermont thermal feedback as controls. Test 0.5 °C and 1.0 °C one-sided planning margins: in Texas penalize forecast indoor temperatures above 25.5 or 25.0 °C during hours 15–18; in Vermont penalize temperatures below 20.5 or 21.0 °C during hours 02–05. Include two additional variants that shift the local temperature feedback target by the same margin, toward cooling or heating as appropriate, starting two hours before that window and ending at its final hour. Every controller is scored against the official 22–26 °C and 20–24 °C bands.

**Selection.** Recompute each home's cumulative degree-hours and the median across the 25 homes for each trajectory. Use the two validation weeks pooled. Keep configurations with CV-RMSE at most one percentage point above the current controller and mean discomfort among the five worst homes no higher than its value. Among those, select the lowest median discomfort; break ties using lower CV-RMSE. The current controller is eligible. Only a new validation winner proceeds to the monthly comparison. This trial does not tune on the monthly results.


**Additional exploratory arm.** After inspecting week one, before inspecting monthly outcomes, added a second screen with the same frozen controls, weeks and guardrails. Both additional candidates use the corresponding 0.5 or 1.0 °C planning buffer plus a home-specific feedback boost during the risk window and its two-hour lead-in. Extrapolate half an hour using the measured indoor trend (clipped to ±2 °C per hour). If that estimate crosses the buffered edge, add up to 6% of rated heat-pump power to the local nominal command, scaled by the fitted thermal response. This targets individual homes, while the first trial's preconditioning shifts the nominal feedback target for all homes. Each screen selects independently on training. Reuse of the validation blocks makes the second screen exploratory.

**Initial screen, pooled validation.** For each entry below, values are median K·h per home, CV-RMSE (%), and mean K·h for the five worst homes, in that order.

| Configuration | Texas (June) | Vermont (January) |
|---|---|---|
| Current climate winner | 17.682 / 32.939 / 76.508 | 39.657 / 46.955 / 125.297 |
| 0.5 °C planning buffer | 17.033 / 32.711 / 76.513 | 38.414 / 47.214 / 130.102 |
| 1.0 °C planning buffer | 16.812 / 32.937 / 77.414 | 37.108 / 47.489 / 131.018 |
| 0.5 °C buffer and preconditioning | 17.769 / 32.746 / 76.015 | 39.837 / 47.227 / 133.268 |
| 1.0 °C buffer and preconditioning | 18.058 / 32.903 / 73.634 | 31.593 / 47.921 / 119.128 |

Texas retained the existing controller at this stage. The 0.5 °C buffer missed the strict worst-five cutoff by about 0.005 K·h per home across two weeks; that tiny difference is not evidence of a practically important regression. The 1 °C buffer with preconditioning passed both constraints in Vermont and advanced to February.

**Home-specific screen, pooled validation.** The Texas 0.5 °C setting produced 6.943 median K·h, 32.622% CV-RMSE, and 62.317 K·h worst-five mean. The 1.0 °C setting produced 5.593 K·h, 33.221%, and 60.474 K·h respectively. Both passed, and the 1.0 °C setting advanced to July. In Vermont, the corresponding values were 43.852 / 43.598 / 160.351 and 39.374 / 43.917 / 161.649. Both Vermont settings reduced tracking error but increased discomfort in the five worst homes and did not advance. All six challenger settings were scored using identical official comfort bands and matched weekly warm-ups within each climate.

**Vermont benchmark.** The 1.0 °C buffer with preconditioning improved January median discomfort by 8.06 K·h. In February, its median increased from 83.53 to 90.10 K·h and the worst-five mean increased from 172.00 to 175.07 K·h. Seven homes improved, while the portfolio accumulated 53 more cold and 66 more hot home-hours. CV-RMSE fell from 56.60% to 56.28%. Retain the existing thermal-feedback controller for Vermont. The median home changed from building 411001 to building 4421, so these medians compare portfolio rankings rather than a single selected house.

**Texas benchmark.** The home-specific 1.0 °C setting reduced the July median from 70.47 to 32.19 K·h (54.3%). CV-RMSE improved from 31.93% to 30.94%. Worst-five mean discomfort fell from 191.27 to 172.95 K·h; following the same five homes that had been worst under the previous policy gave 166.84 K·h. Of 25 homes, 22 improved. Hot home-hours fell from 2,289 to 1,543, cold home-hours from 1,074 to 1,070, and total exceedance from 18.08% to 14.05%. Monthly bias moved from −3.14% to −3.10%. Use this as the Texas benchmark policy. The median shifted from building 471221 to building 21356.

**Verification and limitations.** Independently re-scored the 48 weekly/pooled validation reports and four monthly trajectories. Checked identical loads and temperatures for the incumbent against its previous replay, official comfort bands, the full 25-home rankings, hot/cold home-hours on the monthly runs, tracking and worst-five discomfort. The final choices are based on two previously inspected benchmark months. An independent seasonal evaluation would test transferability.
