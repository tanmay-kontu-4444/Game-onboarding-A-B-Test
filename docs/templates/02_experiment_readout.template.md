<!-- GENERATED FILE: rendered by `python -m src.report` from docs/templates/02_experiment_readout.template.md
     and outputs/tables/results.json. Do not edit by hand; every number comes from the analysis outputs. -->

# Experiment Readout: Short "Learn-by-Playing" Onboarding (`onb_short_tutorial_v1`)

> **SIMULATED DATA.** This is a portfolio project. The game, players and results are synthetic
> (`src/simulate.py`). It is written as a product analytics team would present a readout.

**Audience:** Onboarding PM, Game Director, Monetisation PM · **Analysis:** Product Analytics ·
**Pre-registration:** [01_experiment_plan.md](01_experiment_plan.md)

## TL;DR

**Recommendation: {decision}** (pre-registered decision rule {decision_rule}).
The 3-step onboarding raises **D7 retention from {d7_retained_c} to {d7_retained_t} ({d7_retained_diff}, 95% CI {d7_retained_lo} to {d7_retained_hi}; p {d7_retained_p})**, about **{extra_ret} extra retained players per 100k installs**.
Every guardrail passes except first-week revenue, whose wide CI ({revenue_d7_rel_lo} to {revenue_d7_rel_hi}) cannot rule out the −10% "unacceptable" drop. So we roll out in stages with a holdback, and in parallel test bringing back a shop prompt.

---

## 1. What we tested and why

* **Control:** the current 7-step forced tutorial, which includes a step that walks players through the shop.
* **Treatment:** a 3-step "learn by playing" onboarding with optional tips. The shop step is gone.
* **Why:** the forced tutorial was the biggest day-0 drop-off point. We expected that getting players into real levels sooner would improve early retention. The known risk was lower early monetisation, because players no longer see the shop.
* **Design:** player-level 50/50 randomisation at first launch. Installs from 2026-09-01 to 2026-09-28 were enrolled, with 14 days of follow-up. The analysis is intention-to-treat on **{n_cohort} eligible players** ({n_control} control / {n_treatment} treatment).
* The plan, metrics, thresholds and decision rules were committed before any treatment-vs-control comparison was run.

## 2. Can we trust the data?

| Check | Result |
|---|---|
| Sample-ratio mismatch (final cohort) | control share {srm_share}, χ² p = {srm_p}. **No SRM** (p = {srm_p_assigned} on all in-window assignments). |
| Covariate balance | max \|SMD\| = {max_smd} across platform, device, country and channel. Day-0 sessions: {day0_c} vs {day0_t} (p = {day0_p}). |
| Exclusions (pre-registered, blind to arm) | {excl_outside_enrolment_window} late installs assigned after enrolment closed, {excl_assigned_both_arms} players logged in both arms, {excl_assigned_before_install} assigned before install, {excl_bot_like_activity} bot-like players (>50 sessions/day). None of these steps is unbalanced by arm. |
| De-duplication | {dup_sessions} duplicate session rows and {dup_progression} duplicate progression events removed. |

{block_srm}

## 3. Results scorecard

{block_scorecard}

*Primary: no correction (single pre-registered primary). Secondary: Holm-adjusted across the 3 secondaries.
Guardrails are judged by CI against threshold, not by p-value. Retention metrics use the z-test with a Wald CI;
count and continuous metrics use the Welch t-test. Relative CIs use the delta method.*

![Metric scorecard](../outputs/figures/metric_scorecard.png)

## 4. Results in detail

### 4.1 Primary: D7 retention, significant lift above the design MDE

D7 retention rose by **{d7_retained_diff}** ({d7_retained_rel} relative; relative CI {d7_retained_rel_lo} to {d7_retained_rel_hi}).
The point estimate is above the pre-registered 1.0pp MDE, and the whole CI is above zero. Every robustness check agrees:

{block_variance}

* **Bootstrap** CI ({d7_retained_boot_lo} to {d7_retained_boot_hi}) matches the analytic CI.
* **CUPED** with day-0 sessions narrows the CI by only **{cuped_narrow}**. Day-0 sessions correlate just {cuped_corr_d7} with a binary D7 outcome, which caps the variance reduction at {cuped_vr_d7}. Adding pre-assignment covariates in OLS gives {ols2_narrow}. For a continuous metric that tracks day-0 engagement more closely (levels by D3, correlation {cuped_corr_levels}), CUPED narrows the CI by **{cuped_narrow_levels}** (`outputs/tables/cuped_by_metric.csv`). As pre-registered, the unadjusted estimate remains the confirmatory one.

![Primary effect](../outputs/figures/primary_effect.png)

The lift appears from D1 onwards and persists. The 95% CI of the daily difference excludes zero on {n_days_sig} of 14 days, and the D14 gap is still {d14_diff} (CI {d14_lo} to {d14_hi}):

![Retention curves](../outputs/figures/retention_curves.png)

### 4.2 Secondary metrics: all improved (Holm-adjusted)

* **Tutorial completion:** {tutorial_complete_c} → {tutorial_complete_t} (**{tutorial_complete_diff}**, adj. p {tutorial_complete_padj}). The mechanism works: a shorter flow is completed far more often.
* **Levels completed by D3:** {levels_by_d3_c} → {levels_by_d3_t} ({levels_by_d3_rel}, adj. p {levels_by_d3_padj}; bootstrap CI {levels_by_d3_boot_lo} to {levels_by_d3_boot_hi}; Mann–Whitney p {levels_by_d3_mw_p}). Players reach real gameplay sooner.
* **D1 retention:** {d1_retained_c} → {d1_retained_t} ({d1_retained_diff}, adj. p {d1_retained_padj}).

### 4.3 Guardrails against pre-registered thresholds

{block_guardrails}

* **Revenue per player (first 7 days): INCONCLUSIVE.** The point estimate is {revenue_d7_rel}, but revenue is so heavy-tailed that the CI runs from {revenue_d7_rel_lo} to {revenue_d7_rel_hi}. Its worse bound crosses the −10% threshold. This was expected: at the planned sample the test could only detect a {revenue_mde_rel} revenue change.
* **Purchase conversion: PASS, but read with care.** The point estimate is {purchased_d7_diff} ({purchased_d7_rel}, p {purchased_d7_p}), and the CI's lower bound ({purchased_d7_rel_lo}) stays inside the −10% tolerance. However, the design could only detect about a {purchase_mde_rel} drop, and removing the shop step is a plausible mechanism for a small loss. A pass here is "no evidence of harm", not "evidence of no harm".
* **Avg session length: PASS.** Sessions are significantly shorter ({avg_session_len_rel}, CI {avg_session_len_rel_lo} to {avg_session_len_rel_hi}) but stay within the −5% tolerance. This fits the removal of one long forced-tutorial session.
* **Crash rate: PASS** ({crashed_d7_rel}, CI {crashed_d7_rel_lo} to {crashed_d7_rel_hi}).

## 5. Segments (EXPLORATORY) and novelty

> Pre-specified segments, Benjamini–Hochberg corrected. These results generate hypotheses and **do not change the decision**.

![Segment forest plot](../outputs/figures/segment_forest.png)

{block_segments}

* **Platform is the only dimension with significant heterogeneity** (Cochran's Q p = {het_platform_p}). Android gains **{seg_android}** (CI {seg_android_lo} to {seg_android_hi}), while iOS shows essentially no lift ({seg_ios}, CI {seg_ios_lo} to {seg_ios_hi}).
* By country tier, the lift is concentrated in T2/T3 (T1: {seg_t1}, BH p {seg_t1_padj}). By device, it is larger on low and mid tiers than on high-end devices ({seg_high}, BH p {seg_high_padj}). Neither dimension shows significant heterogeneity (p = {het_country_tier_p} and {het_device_tier_p}).
* Interpretation (hypothesis): the old tutorial cost the most for players on slower devices and in lower-ARPU markets, while experienced, high-end-device players were already getting through it.

**Novelty check.** The D7 lift is consistent across the four enrolment weeks (heterogeneity p = {novelty_q_p}; linear trend {novelty_trend} per week, p = {novelty_trend_p}). Week 4 is lower ({week4_diff}, CI {week4_lo} to {week4_hi}), but within noise. Onboarding is a one-time experience for new installs, so a novelty effect was not expected.

{block_novelty}

![Novelty check](../outputs/figures/novelty_by_week.png)

## 6. Practical significance: what is the lift worth?

{block_practical}

**Assumptions (explicit and adjustable):**

* **A1.** Value of an extra D7-retained player = the observed difference in day 7–34 revenue between D7-retained and non-retained **control** players ({v_ret} vs {v_not}; {n_value_window} control players with a complete day-34 window).
* **A2.** 180-day value ≈ {ltv_mult_ret}× the day 7–34 value, giving **{value_per_ret} per extra retained player**.
* **A3.** Each $1 of lost first-week revenue costs about ${ltv_mult_early} over 180 days (early payers are disproportionately repeat payers).
* **A4.** The observed first-week revenue change is not significant. A positive point estimate is **not** counted as a gain; only downside risk is counted.
* Gross revenue (before store fees) only. No ad revenue, no UA-cost effects, and no virality.

**Reading it:** the central case is positive ({net_central} per 100k installs, from retention alone), but small in absolute terms.
A sustained **first-week revenue drop of {breakeven_drop} or more would wipe it out**, and the revenue CI cannot rule that out. In the pessimistic corner (low end of the lift, worst end of revenue) the change loses money ({net_pess}).
That is why the decision depends on the revenue guardrail rather than on the primary metric. A caveat on A1: the extra retained players are concentrated on Android and lower-tier markets, where spend is lower. So A1 probably *overstates* their value.

## 7. Recommendation

**{decision}.** This follows pre-registered **rule {decision_rule}**: primary significantly positive, no guardrail FAIL, at least one guardrail INCONCLUSIVE (revenue).

1. **Staged rollout:** ship the short onboarding to 90% of new installs and keep a **10% holdback on the old tutorial for 6 weeks**. The holdback keeps a concurrent control so we can go on measuring revenue (pooled with this test) and extend the value window to D30+. Roll back if the cumulative first-week revenue CI moves wholly below −10%.
2. **Follow-up fix experiment** (runs inside the 90%): put back a lightweight, optional **shop prompt after level 5** (a "starter pack" tip). It targets the one mechanism that could plausibly cost revenue, without bringing back the forced flow.
3. **iOS / T1:** no action yet. The flat iOS result is exploratory. Validate it in the holdback before considering a platform-specific flow.

## 8. Limitations

* **Synthetic data.** Every number here comes from a simulation with known built-in effects. Real behaviour is messier: time-zone effects, store-featuring shocks, and seasonality.
* **Short horizon.** We measure D7/D14 retention and first-week revenue. LTV is extrapolated with assumptions A1–A3, so long-term retention could converge between the arms.
* **Guardrail power.** Purchase conversion and revenue are underpowered for their thresholds (minimum detectable change ≈ {purchase_mde_rel} and {revenue_mde_rel}). Absence of evidence is not evidence of absence.
* **Multiple comparisons.** Holm controls the secondaries and BH the segments. Guardrails, curves and the novelty table are uncorrected and should be read as descriptive.
* **Segment findings** are exploratory. Platform heterogeneity could be confounded with country tier, since iOS skews towards T1.
* **Novelty:** onboarding is seen once per player, but we only observe 4 enrolment weeks, so seasonal changes in install mix could hide a slow drift.
* **External validity:** enrolment ran over a single month with the current UA mix. A shift towards paid social or T1 traffic would change the average effect, given the heterogeneity.
* **SUTVA / interference:** we assume none, since the game is single-player in week 1. Social features (guilds, gifting) could leak treatment effects later.
* **Metric definitions:** retention uses UTC calendar days, so a player who installs late in the day gets an easier D1. Average session length is conditional on having at least one session, which is a mild selection risk. Day-0 sessions (the CUPED covariate) are measured after randomisation; balance holds, but it is not a true pre-period variable.
* **Revenue** is gross, excludes refunds, and ignores ad monetisation.

## 9. Next steps

1. Launch the staged rollout with the 10% holdback, and pre-register the holdback readout (first-week revenue and D30 retention) now.
2. Run the shop-prompt follow-up test. Primary metric: first-week purchase conversion. Guardrail: D7 retention must stay within −0.5pp of the short-onboarding arm.
3. Measure D30/D60 retention and revenue in the holdback to replace assumptions A1–A3 with observed values.
4. Investigate the iOS/T1 null result with a targeted analysis (tutorial completion and skip rates by platform) before considering any platform-specific onboarding.
5. Add the onboarding change to the long-term holdout so its effect can be tracked against future UA-mix changes.

---

*Reproduce with `make all`. Tables: `outputs/tables/`. Figures: `outputs/figures/`. Methods: `src/stats_utils.py`, `src/analysis.py`.*
