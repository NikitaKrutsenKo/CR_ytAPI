# CreatorRadar — YouTube Trend Engine Research Specification

**Document status:** Implementation specification for YouTube-only Trend research  
**Scope:** YouTube only  
**Derived from:** `CreatorRadar_TrendEngine_Diploma_Expanded (2).ipynb` and `CreatorRadar_YouTube_TrendEngine_SPEC.ipynb`  
**Purpose:** Define one explicit, reproducible contract for implementing and testing the YouTube part of the Trend Engine before Reddit / News / X integration.

> **Important distinction:** The general Trend Engine diploma specification remains the canonical multi-platform specification.  
> This document defines the explicit **YouTube-only research profile** used to test the YouTube pipeline end-to-end.  
> Any YouTube-only adaptation that is not part of the canonical multi-platform formula is labelled as a **research adaptation** and versioned separately.

---

## 1. Scope and Versioning

Use explicit version identifiers:

- `source_panel_version = "youtube_research_v1"`
- `trend_formula_version = "youtube_research_trend_v1"` (with engagement)
- `trend_formula_version = "youtube_research_trend_noE_v1"` (without engagement)
- `confidence_formula_version = "youtube_research_confidence_v1"`
- `normalization_version = "<versioned baseline id>"` (e.g. `youtube_baseline_research_v1`)

The GUI and persisted data label the final research metric as:

- **YouTube Trend Score**
- code/storage key: `youtube_trend_score`

Do **not** label it as the final production multi-platform `TrendScore_v1`.

---

## 2. YouTube Research Pipeline

```text
Fixed YouTube collection panel
        ↓
eligible items + topic-matched items
        ↓
YouTube incidence x_YT
        ↓
EWMA r_YT
        ↓
velocity v_YT + acceleration a_YT
        ↓
G_YT + Z_YT
        │
Repeated video counter snapshots
        ↓
E_YT + V_YT
        │
Distinct creators
        ↓
B_YT
        │
Evidence quality
        ↓
N / D / F / M / O
        │
YouTube research gate
        ↓
YouTube Trend Score
        ↓
Lifecycle / Analytics / Replay
```

---

## 3. Fixed YouTube Collection Panel

A simple `search(q=<topic>)` result count is **not** true incidence because it contains only matching items.

For comparable Trend testing, each measurement window must contain:

- `n_eligible_yt`: all eligible items observed by the fixed YouTube research panel;
- `n_topic_yt`: deduplicated eligible items resolved to the topic;
- `window_start`;
- `window_end`;
- `source_panel_version`;
- `completeness`.

The panel definition must remain stable within one comparable time-series.

If a true eligible denominator cannot be produced, the engine stores:

- `youtube_activity_count`
- `youtube_incidence = null`
- reason: `NO_ELIGIBLE_PANEL`

Never silently substitute topic search count for incidence.

---

## 4. YouTube Incidence

$$
x_{\mathrm{YT}}
=
10^4
\cdot
\frac{n_{\mathrm{topic,YT}}}
     {n_{\mathrm{eligible,YT}}}
$$

Interpretation: topic items per 10,000 eligible items observed by the configured YouTube panel.

Persisted fields:
- `n_eligible_yt`
- `n_topic_yt`
- `youtube_incidence`
- `source_panel_version`
- `window_start`
- `window_end`
- `completeness`

---

## 5. Measurement Windows

Trend measurements use explicit event-time windows. API polling cadence is not automatically the metric window.

Recommended fast research profile:
- measurement window: **1 hour**
- EWMA half-life: approximately **2 hours**

Every metric snapshot belongs to a concrete UTC window. Missing or failed windows remain missing. They must not become artificial zero-activity windows.

---

## 6. EWMA Smoothing

Let:
- $x_{\mathrm{YT},t}$ be the current YouTube incidence (or activity rate);
- $r_{\mathrm{YT},t}$ be the smoothed rate;
- $H$ be the EWMA half-life;
- $\Delta t$ be the elapsed time in hours.

The time-aware EWMA coefficient is:

$$
\alpha_t = 1 - 2^{-\Delta t/H}
$$

The smoothed rate is:

$$
r_{\mathrm{YT},t} = \alpha_t x_{\mathrm{YT},t} + (1-\alpha_t)r_{\mathrm{YT},t-1}
$$

Persisted fields:
- `youtube_incidence_raw`
- `youtube_activity_ewma`
- `ewma_alpha`
- `ewma_half_life_hours`
- `delta_hours`

If a gap exceeds `max_gap_windows`, derivatives are not linearly interpolated across the outage.

---

## 7. Velocity and Acceleration

### Velocity

$$
v_{\mathrm{YT},t}
=
\frac{
\ln\!\left(1+r_{\mathrm{YT},t}\right)
-
\ln\!\left(1+r_{\mathrm{YT},t-1}\right)
}{
\Delta t
}
$$

Units: $[v_{\mathrm{YT}}] = \mathrm{h}^{-1}$.

### Acceleration

$$
a_{\mathrm{YT},t}
=
\frac{
v_{\mathrm{YT},t}
-
v_{\mathrm{YT},t-1}
}{
\Delta t
}
$$

Units: $[a_{\mathrm{YT}}] = \mathrm{h}^{-2}$.

Persisted fields:
- `velocity`
- `acceleration`
- `previous_rate`
- `current_rate`
- `delta_hours`

Acceleration is diagnostic / lifecycle evidence. It does not receive an independent Trend Score weight without validated ablation evidence.

---

## 8. Growth Component $G_{\mathrm{YT}}$

$$
G_{\mathrm{YT}}
=
\operatorname{clip}
\left(
\frac{\max(v_{\mathrm{YT}},0)}
     {v_{\mathrm{scale,YT}}},
0,
1
\right)
$$

The scaling constant is obtained from a comparable historical YouTube cohort:

$$
v_{\mathrm{scale,YT}}
=
Q_{0.90}
\left(
v_{\mathrm{YT}} \mid v_{\mathrm{YT}}>0
\right)
$$

with an explicit positive floor (default `slope_floor = 0.05`).

Persisted fields:
- `velocity`
- `v_scale_yt`
- `v_scale_version`
- `growth_G_YT`
- `baseline_status`

---

## 9. Burst Component $Z_{\mathrm{YT}}$

Let $x$ denote current activity and $b$ historical baseline activity values.

The robust z-score is:

$$
z_{\mathrm{YT}}
=
\frac{
\ln(1+x)
-
\operatorname{median}\!\left(\ln(1+b)\right)
}{
\max\!\left(
1.4826 \cdot
\operatorname{MAD}\!\left(\ln(1+b)\right),
0.25
\right)
}
$$

The normalized Burst component is:

$$
Z_{\mathrm{YT}}
=
\operatorname{clip}
\left(
\frac{\max(0,z_{\mathrm{YT}})}{4},
0,
1
\right)
$$

Persisted fields:
- `raw_robust_z_yt`
- `burst_Z_YT`
- `baseline_median_log`
- `baseline_mad_log`
- `normalization_version`

---

## 10. YouTube Engagement $E_{\mathrm{YT}}$

Uses **counter deltas**, not lifetime ratios.

For consecutive valid observations of the same video:

$$
\Delta V_i = V_{i,t} - V_{i,t-1}, \quad \Delta L_i = L_{i,t} - L_{i,t-1}, \quad \Delta C_i = C_{i,t} - C_{i,t-1}
$$

Eligibility condition: $\Delta V_i \ge 100$.

Reaction rates:

$$
R^{\mathrm{like}}_i = \frac{\Delta L_i}{\Delta V_i}, \qquad R^{\mathrm{comment}}_i = \frac{\Delta C_i}{\Delta V_i}
$$

Let $q(\cdot)$ denote the ECDF percentile in a versioned comparable historical cohort:

$$
E_{\mathrm{YT}} = 0.70\, q\!\left(R^{\mathrm{like}}\right) + 0.30\, q\!\left(R^{\mathrm{comment}}\right)
$$

Persisted fields:
- `delta_views`
- `delta_likes`
- `delta_comments`
- `like_reaction_rate`
- `comment_reaction_rate`
- `like_percentile`
- `comment_percentile`
- `engagement_E_YT`

If $\Delta V < 100$, engagement is unavailable for that observation (**unavailable is not zero**).

---

## 11. YouTube Breadth $B_{\mathrm{YT}}$

Let $n_{\mathrm{creators}}$ be the number of distinct creators in the measurement window.

$$
B_{\mathrm{YT}} = q\!\left(n_{\mathrm{creators}}\right)
$$

where $q(\cdot)$ is the ECDF percentile in the historical comparable cohort.

Persisted fields:
- `distinct_creator_count`
- `creator_count_percentile`
- `breadth_B_YT`

One creator publishing many videos still counts as one creator.

---

## 12. Dynamic Media Velocity $V_{\mathrm{YT}}$

For a selectively tracked video $i$:

$$
q^{\mathrm{views}}_i = \frac{\max(0, \Delta V_i)}{\Delta t_i}
$$

$$
V_{\mathrm{YT}} = q_{\mathrm{same\ age}}\!\left(q^{\mathrm{views}}_i\right)
$$

Persisted fields:
- `views_per_hour`
- `likes_per_hour`
- `comments_per_hour`
- `video_age_at_observation`
- `media_velocity_V_YT`

Negative counter deltas are platform audit corrections, not negative trend.

---

## 13. Effective Support $N$

$$
N = \min\left(1, \frac{n_{\mathrm{eff}}}{50}\right)
$$

Tracks separately: `raw_item_count`, `deduplicated_item_count`, `effective_sample_size = n_eff`.

---

## 14. YouTube Research Diversity $D$

$$
D = 0.5 \cdot \min\left(\frac{n_{\mathrm{origins}}}{10}, 1\right) + 0.5 \cdot \min\left(\frac{n_{\mathrm{platforms}}}{3}, 1\right)
$$

For YouTube-only mode: $n_{\mathrm{platforms}} = 1 \implies D = 0.5 \min(n_{\mathrm{origins}} / 10, 1) + 1/6$.

---

## 15. Freshness $F_{\mathrm{YT}}$

$$
F_{\mathrm{YT}} = 2^{-\mathrm{age}_{\mathrm{YT}} / H_{\mathrm{YT}}}
$$

Uses the last **successful valid observation**, not the last attempted request.

---

## 16. Completeness $M$

$$
M = \frac{n_{\mathrm{successful\ slots}}}{n_{\mathrm{expected\ slots}}}
$$

An API outage reduces completeness; it must not create artificial zero activity.

---

## 17. Observation Duration $O$

$$
O = \min\left(\frac{T_{\mathrm{effective}}}{3\ \mathrm{h}}, 1\right)
$$

---

## 18. YouTube Evidence Confidence

Base confidence:

$$
C_0 = 0.30 N + 0.20 D + 0.20 F + 0.20 M + 0.10 O
$$

Capped confidence:

$$
C_{\mathrm{YT}} = \min(C_0, C_{\mathrm{source}}, C_{\mathrm{duration}}, C_{\mathrm{policy}})
$$

Display: **YouTube Evidence Confidence** (key: `youtube_evidence_confidence`). It is **evidence quality**, not a viral probability.

---

## 19. YouTube Platform Confirmation $P_{\mathrm{YT}}$

$$
P_{\mathrm{YT}} = \begin{cases} \frac{1}{3}, & \text{if YouTube qualifies} \\ 0, & \text{otherwise} \end{cases}
$$

Qualification requires:
- $n_{\mathrm{eff}} \ge \text{min\_support}$
- $\text{origins} \ge \text{min\_creators}$
- $v_{\mathrm{YT}} > 0$ (positive slope)
- Freshness $F \ge 0.5$
- Completeness $M \ge 0.80$

---

## 20. YouTube Trend Score

### With Engagement:
$$
T_{\mathrm{YT}} = 100 \left(0.45 G_{\mathrm{YT}} + 0.25 Z_{\mathrm{YT}} + 0.15 E_{\mathrm{YT}} + 0.10 B_{\mathrm{YT}} + 0.05 P_{\mathrm{YT}}\right)
$$
`trend_formula_version = "youtube_research_trend_v1"`

### Without Engagement:
$$
T_{\mathrm{YT,noE}} = 100 \left(0.55 G_{\mathrm{YT}} + 0.30 Z_{\mathrm{YT}} + 0.10 B_{\mathrm{YT}} + 0.05 P_{\mathrm{YT}}\right)
$$
`trend_formula_version = "youtube_research_trend_noE_v1"`

Code/storage key: `youtube_trend_score`

---

## 21. Publish / Research Gate

$$
\text{Gate Pass} \iff n_{\mathrm{eff}} \ge 20 \land n_{\mathrm{creators}} \ge 5 \land n_{\mathrm{windows}} \ge 3 \land C_{\mathrm{YT}} \ge 0.45 \land M \ge 0.80
$$

If gate fails:
- `youtube_trend_score_public = null`
- Retain internal `youtube_trend_score` and components for Replay and diagnosis.
- Persist `gate_pass` and `gate_fail_reasons`.

---

## 22. Lifecycle States

States: `WATCHING`, `RISING`, `BREAKOUT`, `PEAK`, `COOLING`, `ARCHIVED`.

- `BREAKOUT`: $T_{\mathrm{YT}} \ge \text{breakout\_score}$ (75) and $v > 0$.
- `PEAK`: from `RISING`/`BREAKOUT` when velocity plateaus ($v \le 0$).
- `RISING`: $T_{\mathrm{YT}} \ge \text{rising\_score}$ (50) and $v > 0$.
- `COOLING`: $v < 0$.
- `WATCHING`: baseline monitoring.
- `ARCHIVED`: retired topic.

Persist state transition timestamp and `lifecycle_reason`.
