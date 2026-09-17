# Developer guide

## Reading order

1. README: installation, component tree and modes.
2. `research/domain.py`: the request/bundle/observation/experiment contracts.
3. `research/application.py`: preflight validation and run creation.
4. `research/execution.py`: lifecycle and a single collection cycle.
5. `research/collection.py` and `research/discovery.py`: ingestion.
6. `research/processing.py`: network-free calculation dispatch.
7. `research/gap.py`, original `metrics/engine.py`, `research/trend.py` and `research/tracking.py`.
8. `research/storage.py` and `research/replay.py`: persistence and reconstruction.
9. `research/gui*.py`: presentation only.

## Where to find behavior

| Question | Code |
|---|---|
| Main entry point? | `python -m research` → `research/__main__.py:main` |
| What happens after Run? | `ResearchConsole.run_experiment` → `ResearchWorker` → `ExperimentManager.run` → `ExperimentRun.execute` |
| Where are HTTP requests? | `youtube/client.py:YouTubeClient.get`; endpoint services never contain formulas |
| Pagination? | `research/discovery.py:YouTubeDiscoveryCollector.collect` for search; `research/gap.py:GapEnricher.enrich` for bounded creator playlists |
| Dedupe? | Discovery ID dictionary, bounded request ID dedupe in `YouTubeVideoService`; cross-time observations stay separate |
| CollectionBundle creation? | `YouTubeDiscoveryCollector.collect` and `CollectionService.track` |
| Time windows/watermark? | `CollectionRequest.bounds`, `CollectionService.discover` |
| Gap enrichment? | `GapEnricher.enrich`; original mathematical engine remains unchanged |
| Trend enrichment? | `TrendEnricher.ingest/window/covered`; counter input from `TrackedVideoRegistry.observe` |
| Formulas? | `metrics/engine.py`, `metrics/calculators.py`, `research/trend.py`; provenance in FORMULAS.md |
| Parameters? | `config/formulas/*.json`; complete defaults frozen by `configuration.resolve_formulas` |
| Quota counting? | `QuotaManager.consume` called before each HTTP attempt |
| Cost preflight? | `QuotaManager.estimate`; conservative cold-cache plan and retry allowance |
| State? | `experiments/<id>/state/<topic_id>/`; live state objects in MetricProcessor and CollectionService |
| Raw data? | `data/topics/<id>/raw/<batch_id>.json`; manifests in experiments |
| Chart inputs? | `AnalyticsPanel.load_rows` reads `results/<topic>/*.jsonl` |
| Replay? | `ReplayService.run` verifies references/hashes and instantiates fresh MetricProcessor; no network dependencies are constructed |
| Topic selection? | `TopicScheduler.choose`; selection inputs/reasons in experiment `events.jsonl` |
| Historical evaluation? | `HistoricalAnalyzer.publications`; `EventEvaluator.evaluate/aligned` only after scoring |
| Run reports? | `ExperimentRun._finish`, `ProductReport.build` |

## Debug one cycle in PyCharm

Set Python interpreter to:

`F:\!Life\Business\CreatorRadar\CR_API\.venv\Scripts\python.exe`

Create a Python run configuration with **Module name** `research`, working directory `F:\!Life\Business\CreatorRadar\CR_API`, parameters `--debug`. Use breakpoints in `ExperimentManager.run`, `ExperimentRun._cycle`, `YouTubeClient.get`, `YouTubeDiscoveryCollector.collect`, `GapEnricher.enrich` and `MetricProcessor.process`.

For an offline reproducible debug session, run `tests/test_research_flows.py` or `tests/test_research_gui.py` with pytest. Fakes return deterministic API shapes, including real pagination contracts. Do not enable urllib3 DEBUG or inspect/copy secret-bearing request URLs into logs.

The UI's Run log shows progress; `data/research.log` contains technical diagnostics. Experiment `events.jsonl` records experiment/collection/enrichment/scheduler stages. `telemetry.jsonl` records endpoint, attempts, status, item counts, timing, profile, topic, experiment and operation. Search pagination logs show page/result counts. Unexpected worker exceptions are logged and displayed briefly.

## Add or modify a metric

Keep API calls out of the engine. Add a typed parameter to the corresponding config, a documented deterministic calculation and an output field. Explain units, missing semantics, normalization cohort and any dependence on state. Bump formula version for mathematical changes. Add a fixture derived from the specification, and a replay equivalence or causality test. Update FORMULAS.md and a config preset. Numeric JSONL fields automatically become available in Analytics; nonnumeric lifecycle/gate details appear in the details panel.

New output columns should be added only to new experiment workspaces; do not append a different CSV header to an existing run. Raw schema changes require a new schema version and an explicit reader/migration. Never rewrite existing observations to fit a new formula.

## Add an experiment mode

Add a `Mode` enum member, validation policy, cost estimate and dispatch in `MetricProcessor`/`ExperimentRun`. Keep setup and orchestration separate from calculation. Decide whether it requires discovery, tracking, Gap baseline calls or no network at all. Add fake-client integration coverage showing which endpoints are and are not called. Update the GUI and mode instructions. Modes should share CollectionBundle instead of creating a second search pipeline.

## Add a source later

Implement a real source adapter with honest timestamps, normalized source-native counters and completeness metadata. Define a source panel version and comparable historical cohort. Do not sum raw YouTube and Reddit counts or renormalize absent production sources implicitly. If a fixed panel can supply true eligible counts, introduce a separate incidence contract. The current YouTube query-series semantics and raw schema must remain reproducible.

## Templates, events and scientific workflow

Save template captures the full ExperimentConfig, including formula values and the selected profile name. Load template restores settings; imported formula values are materialized in `data/imported_formulas`. The desktop form uses common collection settings for its semicolon-separated topic list; the backend contract can represent per-topic requests. Hand-authored heterogeneous per-topic templates should be run via `ExperimentManager`, because the current form exposes only one shared window/page setting.

KnownEvent import expects a JSON array. Each object requires `topic_id`, `event_name`, `event_type`, `event_time` (aware UTC), `description`, `source_url`, `source_type`, and `verified_at`. Obtain the real topic ID from the experiment's `topics.json`. Verify dates against traceable official sources yourself; importing is not automatic external verification. Use synthetic fixtures only in tests and mark them synthetic.

Prepare positive/medium/weak/control templates without giving event dates or outcomes to engines. Publication-history analysis is a sampling diagnostic; true velocity validation requires stored historical counter observations. Event-relative charts and lead-time evaluation occur only after computation.

## API, storage and compatibility invariants

- Raw writes use exclusive creation; replay verifies checksums.
- Optional counters are missing, not zero. Negative deltas are corrections.
- Gap eligibility cannot filter a CollectionBundle or Trend input.
- Repeated observations are retained; only within-discovery IDs are deduplicated.
- Discovery is the only search workflow. Tracking calls videos only.
- One run has one profile. No key is persisted or automatically switched.
- Failed/capped collections cannot advance incremental discovery watermarks.
- Trend/Gap state and separate experiments are isolated.
- KnownEvent never enters either engine.
- Replay does not construct live infrastructure.
- Schema 1 uses the legacy replay entry point; schema 2 rejects unknown versions.

## Validation and maintenance

Run `python -m pytest -q` after installing requirements-dev.txt. Tests never require credentials or network. Qt tests set the offscreen platform; a Windows font is loaded explicitly for headless rendering. For a real visual inspection launch the GUI normally. Headless smoke images under `data/` are ignored, not source artifacts.

Run the README Ruff command for the new research surface; original PoC modules are intentionally not reformatted wholesale. Follow existing Git constraints: base origin/alpha_test, feature branch only, review diff and stage exact files, keep commits local, preserve .idea and all unrelated work.

Known engineering limits: no automatic process resume, replay initially loads its manifest into memory, no external normalization corpus, coarse age cohorts, bounded heuristic tracking shortlist, and no arbitrary separate-axis chart editor. These are explicit PoC boundaries, not hidden substitutes for unavailable data.
