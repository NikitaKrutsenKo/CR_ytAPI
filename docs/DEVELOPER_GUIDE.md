# Developer Guide

Welcome to the CreatorRadar Research developer guide. This document explains how to set up, run, debug, test, and extend the research codebase.

For architecture and system layout, see [ARCHITECTURE.md](file:///d:/Programming/Projects/GitHub/CR_ytAPI/docs/ARCHITECTURE.md).  
For the end-to-end data lifecycle and enrichment specifications, see [DATA_PIPELINE.md](file:///d:/Programming/Projects/GitHub/CR_ytAPI/docs/DATA_PIPELINE.md).  
For mathematical definitions, see [FORMULAS.md](file:///d:/Programming/Projects/GitHub/CR_ytAPI/docs/FORMULAS.md).

---

## 1. Quick Start & Setup

### Environment Setup
```powershell
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install runtime and development dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Create local environment config
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

### Configure Credentials
Edit `.env` and add your YouTube Data API key:
```dotenv
YOUTUBE_API_KEY_DEFAULT=your_api_key_here
CREATORRADAR_DEFAULT_API_PROFILE=DEFAULT
```
> [!NOTE]
> API keys are never written to logs, telemetry, git, or experiment metadata. Only the profile name (`DEFAULT`) is recorded.

### Launch Application
```powershell
# Launch the PySide6 Research Console
python main.py

# Or launch with debug logging
python main.py --debug
```

---

## 2. Recommended Reading Order

1. **[README.md](file:///d:/Programming/Projects/GitHub/CR_ytAPI/README.md)**: High-level overview and feature summary.
2. **[DATA_PIPELINE.md](file:///d:/Programming/Projects/GitHub/CR_ytAPI/docs/DATA_PIPELINE.md)**: How data is ingested, enriched, computed, and saved.
3. **`research/core/domain.py`**: Immutable domain models, requests, observations, bundles, and configs.
4. **`research/orchestration/application.py` & `execution.py`**: Experiment preflight, quota validation, and execution loop.
5. **`research/orchestration/collection.py` & `research/api/discovery.py`**: Discovery and tracking collection services.
6. **`research/metrics/gap.py` & `research/metrics/trend.py`**: Gap baseline enrichment and Trend mathematical engines.
7. **`research/metrics/processing.py`**: Network-free calculation boundary.
8. **`research/storage/workspace.py`**: Isolated experiment and topic workspace manager.

---

## 3. Code Navigation Map

| What are you looking for? | Where is it implemented? |
|---|---|
| **Application entry point** | `main.py` and `research/__main__.py` → `research.gui.console:main` |
| **GUI Windows & Widgets** | `research/gui/console.py` (Main window), `form.py` (Form), `analytics.py` (Charts) |
| **HTTP client & API calls** | `research/api/client.py:YouTubeClient` (sole HTTP boundary) |
| **Quota ledger & budgets** | `research/api/quota.py:QuotaManager` (SQLite backed) |
| **Topic discovery logic** | `research/api/discovery.py:YouTubeDiscoveryCollector` |
| **Counter tracking logic** | `research/orchestration/collection.py:CollectionService.track` |
| **Creator baseline enrichment** | `research/metrics/gap.py:GapEnricher` |
| **Gap math calculations** | `research/metrics/gap.py:GapEngine` and `research/metrics/calculators.py` |
| **Trend math calculations** | `research/metrics/trend.py:TrendEngine` |
| **Trend windowing/coverage** | `research/metrics/trend.py:TrendEnricher` |
| **Offline replay service** | `research/orchestration/replay.py:ReplayService` |
| **Workspace persistence** | `research/storage/workspace.py:TopicWorkspaceManager` |
| **Atomic file I/O & cleanup** | `research/storage/io.py:write_json, iter_jsonl, cleanup_temporary_files` |

---

## 4. Testing & Verification

### Running Automated Tests
The repository uses `pytest` with comprehensive unit and integration tests. All tests run locally without network access or live API credentials:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### Static Analysis & Linting
The project uses `ruff` for code quality, formatting, and import sorting:

```powershell
.\.venv\Scripts\python.exe -m ruff check research main.py tests
```

---

## 5. Debugging an Experiment Cycle

To debug an experiment run in PyCharm or VS Code:
1. Set the interpreter to `.\.venv\Scripts\python.exe`.
2. Create a launch configuration with module `research` and arguments `--debug`.
3. Useful breakpoints:
   - `ExperimentManager.run`: Preflight validation and configuration snapshot.
   - `ExperimentRun._cycle`: Start of a discovery or tracking cycle.
   - `YouTubeDiscoveryCollector.collect`: Search pagination and batch video detail reads.
   - `GapEnricher.enrich`: Creator baseline lookup, playlist retrieval, and cache checks.
   - `MetricProcessor.process`: Calculation dispatch to GapEngine and TrendEngine.

### Diagnostics & Logs
- **Console Log Tab**: The GUI displays real-time progress events.
- **Disk Log**: `data/research.log` contains detailed rotating logs (10MB, up to 3 backups).
- **Run Telemetry**: `experiments/<id>/telemetry.jsonl` tracks duration, HTTP status, and items for every API call.
- **Lifecycle Events**: `experiments/<id>/events.jsonl` logs state transitions and topic scheduling decisions.

---

## 6. Extending the Codebase

### Adding or Modifying a Metric Formula
1. **Never call the network inside metric classes**: Metric calculators must remain strictly network-free.
2. Add typed configuration fields to `ExperimentConfig` in `research/core/domain.py`.
3. Implement deterministic calculation in `research/metrics/calculators.py` or `research/metrics/trend.py`.
4. Include clear textual formulas in docstrings and update `docs/FORMULAS.md`.
5. Write unit tests validating boundary behaviors (e.g. division by zero, empty inputs, extreme percentiles).
6. Metrics output automatically into `results/<topic_id>/<kind>.jsonl` and companion `.csv`.

### Adding a New Experiment Mode
1. Add a new enum value to `Mode` in `research/core/domain.py`.
2. Define its quota estimation in `QuotaManager.estimate()`.
3. Update `MetricProcessor.process()` and `ExperimentRun._jobs()` to dispatch the mode's required jobs.
4. Add corresponding test coverage in `tests/test_research_flows.py`.
