# Implementation report — CreatorRadar Research Console

Date: 2026-09-18.

Local project: `F:\!Life\Business\CreatorRadar\CR_API`.

Local feature branch: `research_gui_trend_gap`.

Base: the existing local `origin/alpha_test` ref. `main` was not used as the development base. The repository was not cloned. Existing `.idea` files were preserved. Four schema-1 files already tracked by the base under `data/` were also preserved unchanged; the runtime ignore rules apply to new artifacts, not removal of the historical files. No push, PR, remote branch, tag or release operation was performed.

## Delivered application

The existing Gap PoC now has a PySide6/pyqtgraph desktop application. The legacy Gap engine, CLI, dataset builder and schema-1 replay remain available. New application/domain/infrastructure boundaries provide shared discovery, independent Gap enrichment and Trend calculations, experiment workspaces, local quota accounting, scheduling, historical publication analysis, event overlays and offline formula comparison.

Main component responsibilities and exact files are documented in README's component tree and ARCHITECTURE.md. The main execution path is:

`ExperimentForm → ResearchWorker → ExperimentManager → ExperimentRun → CollectionService → shared CollectionBundle → MetricProcessor → Gap/Trend outputs → Analytics`.

`ReplayService → verified stored bundle/enrichment → fresh MetricProcessor` has no network client or API profile resolution. `KnownEvent → EventEvaluator/Analytics` is separate from scoring.

## Functionality by scenario

| Scenario | Result |
|---|---|
| A: Quick Gap, AI laptop, rolling 24h | GUI request, shared collection, creator eligibility/cache, original score and intermediate metrics. Verified with fake responses; live credentials needed for a real dataset. |
| B: Quick Trend, GTA VI, repeated observations | Fixed publication buckets, EWMA, actual elapsed-time derivatives, causal G/Z, same-age reaction ECDFs, breadth, research confidence and heuristic lifecycle. Evidence gates can leave scores null. |
| C: Combined | One discovery stream feeds both engines. A failed/ineligible Gap baseline cannot delete Trend observations. |
| D: Product Simulation | Manual/preset topic pools, deterministic exploration/monitoring, selection reasons, independent tracking cadence, quota stops and research report. |
| E: Historical | Publication-history sample with separately verified event annotations, calendar/event-relative axes and post-calculation evaluation. Historical counter trajectories are never fabricated. |
| F: Replay | Integrity-verified raw references plus captured baselines, full frozen parameter sets, new result workspace, no API calls; multiple presets plotted for comparison. |

YouTube optimizations: bounded token pagination, per-batch dedupe, incremental overlap, batched video/channel reads, bounded playlist reads, TTL creator cache, separate discovery/tracking and finite backoff. API profile names are persisted; secrets are not. The quota manager has independent search/read pools, atomic per-profile Pacific-day accounting, retry debits, conservative preflight and per-attempt runtime guard. The 1.45% empirical observation is not converted into a hardcoded cycle count.

Raw/derived storage layout, exact Windows setup, `.env` variables, GUI launch and per-mode walkthroughs are all in [README](../README.md). Main launch:

```powershell
cd "F:\!Life\Business\CreatorRadar\CR_API"
.\.venv\Scripts\python.exe -m research
```

The local `.venv` was created and runtime/development dependencies installed. No real API key was added and no live quota was consumed.

## Verification evidence

- Original baseline: 12 tests passed before feature development.
- Final suite: **52 passed**, including original regressions, ingestion failures, quota resets/guards, cache behavior, scientific time/normalization behavior, all major modes, raw/config/baseline integrity, replay and offscreen GUI integration.
- `ruff check research youtube/client.py youtube/search.py youtube/videos.py tests/test_research*.py`: passed.
- `compileall` over research and existing source packages: passed.
- `python -m research --help`: passed.
- `pip check`: no broken requirements.
- GUI instantiated and configured in offscreen Qt; Run→worker→fake backend→saved results→Analytics chart tested. A readable Windows-font screenshot was visually inspected.
- Fake HTTP tests cover nextPageToken, overlapping IDs, empty results, missing/deleted statistics, 429/500 retries, fatal 401/403, server quota exhaustion, cancellation and partial collections.
- Replay tests forbid `requests.Session.get`, compare live/replay metrics and verify raw bytes remain unchanged.
- Gating prevents unavailable data from becoming a zero signal. The notebook's EWMA example is a numerical regression test.
- Real YouTube response behavior, credential validity and multi-hour desktop operation were **not** tested against the live service. A user-run real experiment is the next integration step.

Ignored local QA images: `data/gui-smoke.png`, `data/gui-analytics.png`. They are development evidence, not checked-in assets or real research observations.

## Scientific limits, explicitly preserved

1. The production notebook score needs fixed-panel incidence and other sources. Query search cannot supply that denominator; Reddit/News are not implemented. `trend_score` is null, not an invented production number.
2. `youtube_research_score` is a separately named/versioned experimental adaptation. Weights, source cap, origin-capped effective support, local/coarse-age cohorts and lifecycle thresholds are documented in FORMULAS.md and need backtest.
3. Capped or incomplete collections cannot establish complete publication-window evidence. Warm-up, missing counters or insufficient independent creators can keep components/scores unavailable.
4. Historical discovery only reconstructs sampled publication history. True historical counter velocity, engagement, acceleration and Gap require actual stored past observations.
5. No verified real-event dataset is fabricated or bundled. Users supply traceable KnownEvent records and must include negative/control cases for meaningful evaluation.
6. Automatic live-process restart/resume, an external normalization corpus and a production selective-activation policy are not implemented. Tracking uses an explicitly documented bounded research shortlist.
7. Legacy CLI collection is retained for compatibility and does not use the desktop quota ledger. Local quota cannot know outside Cloud-project usage. Different profile names do not imply independent project budgets.
8. Replay verifies the experiment in memory before processing; extremely large archives may need a streaming index. Topic templates in the GUI share window/page settings; heterogeneous backend request templates require direct application-service use.
9. Comparison chart shows first-topic EWMA; other result metrics are accessible per experiment in Analytics. Two-metric overlays use a shared scale; no general chart designer is claimed.

These are disclosed scope/data limitations, not hidden approximations. The application is a research tool and should not be treated as validated production trend detection.

## Recommended next steps

Configure a tester's `.env`, run one short Quick Gap and Quick Trend experiment, then inspect raw status/coverage and quota telemetry. Collect enough complete windows for causal normalization. Build a manually verified positive/medium/weak/control event set and keep tuning separate from held-out evaluation. Add a true discovery panel and real additional sources only when ready to implement the notebook's production score semantics.

Documentation: README, ARCHITECTURE.md, DEVELOPER_GUIDE.md, FORMULAS.md and AUDIT.md. Public classes and non-obvious calculations carry docstrings and invariants near the code. Source/commit inventory below is generated from the actual checkout.

## Final source tree

```text
.env.example
.gitignore
LICENSE
README.md
build_dataset.py
config.py
config/formulas/gap_v1.json
config/formulas/trend_balanced.json
config/formulas/trend_conservative.json
config/formulas/trend_sensitive.json
data/latest_batch.json
data/metrics_timeseries.csv
data/raw_batches.jsonl
data/state.json
docs/ARCHITECTURE.md
docs/AUDIT.md
docs/DEVELOPER_GUIDE.md
docs/FORMULAS.md
docs/IMPLEMENTATION_REPORT.md
main.py
metrics/README.md
metrics/__init__.py
metrics/calculators.py
metrics/engine.py
models/__init__.py
models/batch.py
models/metrics.py
models/raw_video.py
models/time_utils.py
replay.py
requirements-dev.txt
requirements.txt
research/__init__.py
research/__main__.py
research/application.py
research/collection.py
research/configuration.py
research/discovery.py
research/domain.py
research/evaluation.py
research/execution.py
research/gap.py
research/gui.py
research/gui_analytics.py
research/gui_form.py
research/processing.py
research/profiles.py
research/quota.py
research/replay.py
research/reporting.py
research/scheduling.py
research/storage.py
research/tracking.py
research/trend.py
ruff.toml
scheduler.py
state/README.md
state/__init__.py
state/manager.py
storage/README.md
storage/__init__.py
storage/csv_writer.py
storage/dataset_builder.py
storage/json_writer.py
storage/raw_batch_store.py
storage/validator.py
tests/__init__.py
tests/test_collector_helpers.py
tests/test_dataset_builder.py
tests/test_metrics.py
tests/test_research_flows.py
tests/test_research_gui.py
tests/test_research_ingestion.py
tests/test_research_resilience.py
tests/test_scheduler.py
tests/test_storage_and_validation.py
youtube/__init__.py
youtube/channels.py
youtube/client.py
youtube/collector.py
youtube/search.py
youtube/videos.py
```

## New and modified files against origin/alpha_test

```text
A	.env.example
M	.gitignore
M	README.md
M	config.py
A	config/formulas/gap_v1.json
A	config/formulas/trend_balanced.json
A	config/formulas/trend_conservative.json
A	config/formulas/trend_sensitive.json
A	docs/AUDIT.md
M	metrics/README.md
A	requirements-dev.txt
M	requirements.txt
A	research/__init__.py
A	research/__main__.py
A	research/application.py
A	research/collection.py
A	research/configuration.py
A	research/discovery.py
A	research/domain.py
A	research/evaluation.py
A	research/execution.py
A	research/gap.py
A	research/gui.py
A	research/gui_analytics.py
A	research/gui_form.py
A	research/processing.py
A	research/profiles.py
A	research/quota.py
A	research/replay.py
A	research/reporting.py
A	research/scheduling.py
A	research/storage.py
A	research/tracking.py
A	research/trend.py
A	ruff.toml
M	state/README.md
M	storage/README.md
A	tests/test_research_flows.py
A	tests/test_research_gui.py
A	tests/test_research_ingestion.py
A	tests/test_research_resilience.py
M	youtube/client.py
M	youtube/search.py
M	youtube/videos.py
A	docs/ARCHITECTURE.md
A	docs/DEVELOPER_GUIDE.md
A	docs/FORMULAS.md
A	docs/IMPLEMENTATION_REPORT.md
```

## Local implementation commits before the final verification commit

```text
b1e9562 docs: document research architecture, formula provenance and Windows workflows
1fe51d9 fix: freeze formula defaults and verify reproducible replay configuration
7b13874 feat: add desktop experiment console, analytics and replay comparison
6cb03cc refactor: encapsulate run lifecycle and harden scientific data boundaries
3fb625c feat: orchestrate research modes, scheduling and offline replay
648a079 feat: implement causal YouTube Trend research components
5b0ea7e feat: persist immutable topic observations and isolate Gap enrichment
50856a9 feat: add shared discovery contracts, API profiles and guarded ingestion
fd32d26 docs: record research architecture audit and migration plan
```

Final code verification commit: `8b118bd` (deadline/freshness safeguards). The following documentation-only commit clarifies preserved baseline data. Run `git log --oneline --decorate --graph -30` for the final hash.
