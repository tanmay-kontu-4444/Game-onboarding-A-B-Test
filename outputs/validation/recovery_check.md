# Effect recovery check (validation only - SIMULATED DATA)

True effects come from `data/raw/ground_truth.json`; estimates from `outputs/tables/results.json`.

## Metrics

| metric            | role      |   true_abs_diff |   estimate |    ci_low |   ci_high | in_95ci   |   z_error | in_bonferroni_ci   |
|:------------------|:----------|----------------:|-----------:|----------:|----------:|:----------|----------:|:-------------------|
| d7_retained       | Primary   |         0.01200 |    0.01160 |   0.00619 |   0.01700 | True      |  -0.14692 | True               |
| tutorial_complete | Secondary |         0.17439 |    0.17633 |   0.16997 |   0.18269 | True      |   0.59845 | True               |
| levels_by_d3      | Secondary |         0.40079 |    0.42651 |   0.33027 |   0.52275 | True      |   0.52392 | True               |
| d1_retained       | Secondary |         0.00911 |    0.01492 |   0.00814 |   0.02171 | True      |   1.67909 | True               |
| purchased_d7      | Guardrail |        -0.00119 |    0.00095 |  -0.00119 |   0.00308 | False     |   1.96034 | True               |
| revenue_d7        | Guardrail |        -0.01023 |    0.01278 |  -0.02219 |   0.04775 | True      |   1.29003 | True               |
| crashed_d7        | Guardrail |         0.00197 |    0.00029 |  -0.00349 |   0.00407 | True      |  -0.87119 | True               |
| avg_session_len   | Guardrail |       -10.03297 |  -10.96787 | -14.87136 |  -7.06438 | True      |  -0.46942 | True               |

## D7 effect by segment

| dimension    | segment   |   true_abs_diff |   estimate |   ci_low |   ci_high | in_95ci   | in_bonferroni_ci   |
|:-------------|:----------|----------------:|-----------:|---------:|----------:|:----------|:-------------------|
| platform     | Android   |         0.01589 |    0.02011 |  0.01304 |   0.02719 | True      | True               |
| platform     | iOS       |         0.00723 |    0.00081 | -0.00752 |   0.00914 | True      | True               |
| device_tier  | high      |         0.00631 |    0.00495 | -0.00468 |   0.01458 | True      | True               |
| device_tier  | low       |         0.02042 |    0.01351 |  0.00361 |   0.02340 | True      | True               |
| device_tier  | mid       |         0.01117 |    0.01576 |  0.00714 |   0.02438 | True      | True               |
| country_tier | T1        |         0.00608 |    0.00411 | -0.00607 |   0.01430 | True      | True               |
| country_tier | T2        |         0.01494 |    0.01238 |  0.00324 |   0.02153 | True      | True               |
| country_tier | T3        |         0.01417 |    0.01693 |  0.00807 |   0.02579 | True      | True               |
