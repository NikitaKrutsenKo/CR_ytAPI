# Formula provenance and research semantics

## Source of truth

User-supplied notebook:

`F:\!Life\Business\CreatorRadar\MathCore\CreatorRadar_TrendEngine_Diploma_Expanded.ipynb`

Notebook title/version: Trend Engine — від сигналів платформ до Trend Score, version 2.0, 2026-09-12.

SHA256 inspected for this implementation:

`655BA09D6AE6D491F510C879B55C688CBC7ACA4B44BAD4A2508AE8F13E9D1834`

The notebook is not copied or modified. Its source panel uses YouTube, Reddit and News; this repository only implements YouTube. Research adaptations below are explicit and need held-out validation. They are not claims of production equivalence.

## Gap: original mathematics preserved

`research.gap.GapEngine` inherits the existing `metrics.engine.MetricEngine` without changing its formulas. Configuration is `config/formulas/gap_v1.json`, copied in full into each experiment. The original engine uses engagement/performance normalization and creator authority; `Gap = demand_norm * (1 - supply_norm)`. Intermediate rates, low/high boundaries, ER/PR, demand, supply, authority and qualifying counts remain in the snapshot.

The research enrichment adapter excludes the whole thematic batch from creator baselines and refuses missing counters. Cache ages are measured at the cache observation time. This avoids dividing an old counter by an artificially newer age. A historical publication request does not supply historical Gap counters.

## Measurement and missingness

- Search count is `youtube_activity_count`, never incidence. No eligible panel denominator exists.
- `youtube_activity_rate = unique publications in the completed bucket / window_hours`.
- Buckets are fixed event-time windows (default 1h), independent of discovery polling (default 30m).
- Only fully covered, complete and uncapped buckets enter the valid baseline. Uncovered/capped/failed buckets have null activity, derivatives and research score. `sampled_video_count` remains available diagnostically.
- `timestamp` is the real collection completion/decision time; `window_end` records the measurement boundary. Known Event lead time uses the decision timestamp.
- No future values enter normalization. Baselines contain prior accepted windows only; reaction ECDFs use prior intervals only.

## Notebook components implemented

| Component | Formula / behavior | Lineage |
|---|---|---|
| EWMA | `alpha = 1 - 2**(-dt/H)`; `r = alpha*x + (1-alpha)*previous_r` | §4, applied to named YouTube activity proxy |
| Velocity | `(log1p(r) - log1p(previous_r))/dt` | §6 |
| Acceleration | `(velocity - previous_velocity)/dt` | §6, diagnostic/lifecycle only |
| Growth | `clip(max(v,0)/max(prior_positive_slope_q90, floor),0,1)` | §7 |
| Burst | `z=(log1p(r)-median(log1p(prior_r)))/max(1.4826*MAD,0.25)`; `Z=clip(max(z,0)/4,0,1)` | §8 |
| Engagement | `0.7*ECDF(delta_likes/delta_views)+0.3*ECDF(delta_comments/delta_views)` | §9, only delta_views >=100, both reaction counters present |
| Breadth | Prior ECDF of distinct creator count | §10 |
| Support N | `min(n_eff/50,1)` | §12.2 |
| Diversity D | `0.5*min(origins/10,1)+0.5*(1/3)` for the one real platform | §12.3 |
| Freshness F | Successful observation freshness; failures show exponential diagnostic decay and never score | §12.4 |
| Completeness M | Accepted windows / expected slots, including skipped slots | §12.5 |
| Duration O | `min(valid_observation_hours/3,1)`; outage hours do not accumulate | §12.6 |
| Research confidence | `min(.3*N+.2*D+.2*F+.2*M+.1*O, source_cap)` | Production component weights, explicit research cap default .6 |
| Counter diagnostics | Actual deltas and elapsed-hour rates | §15 |

The EWMA regression example is tested: previous=20, current=40, H=2h, dt=1h gives approximately 25.8579. Missing/late windows reset derivative continuity; three contiguous accepted windows are required by the research publish gate.

## Explicit research decisions where inputs/specification are incomplete

1. **Activity proxy:** G/Z use YouTube publication activity, not production fixed-panel incidence or a weighted multi-platform rate. Formula versions begin `youtube_activity_research_*`.
2. **Comparable cohort:** this local PoC uses prior observations of the same query/topic and fixed window plan. It has no external reference corpus. Reaction ECDFs use coarse age buckets `0_24h`, `24_168h`, `168h_plus`. All items in one poll compare against the same prior cohort before it is updated. This is a research approximation, not a calibrated category-wide cohort.
3. **Effective support:** each distinct creator contributes at most one unit in the rolling 24h sample, so `n_eff = distinct creators`. This deliberately conservative origin cap is explicit; it is not an invented independent-item count or a statistically validated ESS estimator.
4. **History limits:** up to 168 valid windows and 1,000 past reaction ratios per age bucket. Initial G/Z/breadth require at least 3 prior windows by default. Broad/capped queries may never meet complete-evidence gates until query/window/page settings are improved.
5. **No production P or aggregate:** Reddit/News are unavailable, not zeros. `trend_score` remains null. The source panel is not dynamically renormalized.
6. **Separate research score with E:** `100*(.45*G+.25*Z+.15*E_YT+.10*B_YT)/.95`. This reduction is an explicitly named experimental formula.
7. **Separate research score without E:** `100*(.55*G+.30*Z+.15*B_YT)`. `research_gate=PASS_NO_ENGAGEMENT_v1` identifies the variant; it does not pretend E=0. These weights are heuristic, not an unmodified notebook production formula.
8. **Research publish gate:** G/Z/B available, origin-capped support >=20, origins >=5, three contiguous valid windows, confidence >=.45 and completeness >=.8. Confidence is never multiplied into score. Failed gates retain components and null score.
9. **Lifecycle:** notebook names states but does not supply exact transition thresholds here. The research policy uses configurable rising/breakout score thresholds 50/75 with positive velocity; active growth reaching nonpositive velocity enters PEAK; falling low scores enter COOLING; otherwise WATCHING. Gate failure keeps the previous state without claiming a new transition. This is a versioned research heuristic requiring backtest. ARCHIVED automation is not implemented.
10. **Tracking activation:** bounded recency/view-count shortlist per topic, including warm-up. Production catalyst and cross-platform activation gates are not fabricated. `views_per_hour` is not assigned an additional score weight.

Negative counter changes are corrections/resets: raw negative deltas and a correction flag are preserved; affected rates and reaction eligibility are unavailable. Lifetime totals never masquerade as true counter velocity.

## Configuration and comparisons

`gap_v1.json` supplies original Gap parameters. Trend balanced/sensitive/conservative JSON files vary half-life, slope floor, baseline warm-up and heuristic lifecycle thresholds. `research.configuration.resolve_formulas` materializes every default before snapshot persistence. A change to parameters yields a new config hash; a change to mathematics or source semantics needs a new formula version.

Compare settings by replaying the same intact raw experiment. Use disjoint tuning and evaluation time periods and include weak/control cases. Strong-looking curves, research scores and event lead times alone do not establish predictive performance.
