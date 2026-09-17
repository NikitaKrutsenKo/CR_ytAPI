# CR:Correlator YouTube PoC

PoC pipeline:

`YouTube Data API -> raw YouTubeBatch -> Metric Engine -> metrics CSV`

## Current components

- YouTube collector for thematic videos
- Recent creator video collection: 5..10 videos, excluding all videos from the current thematic batch
- Gap Score Metric Engine
- Persistent metric state in `state.json`
- Append-only raw batch storage in JSONL
- Calculated time-series storage in CSV
- Validation of raw batches, metric snapshots and metrics CSV
- Live scheduler mode
- Replay mode from stored raw batches

## Setup

```bash
pip install -r requirements.txt
```

Create `.env`:

```env
YOUTUBE_API_KEY=your_api_key
GAP_HALF_LIFE_HOURS=12
GAP_EPSILON=0.000001
GAP_WEIGHT_ER=0.5
GAP_WEIGHT_PR=0.5
GAP_EXTREME_PERCENT=0.10
```

## One collection cycle

```bash
python main.py --topic "iphone 17" --max-videos 50 --output data/batch.json
```

## Repeated collection

```bash
python scheduler.py --topic "iphone 17" --max-videos 50 --interval-minutes 30
```

Use `--once` for exactly one cycle:

```bash
python scheduler.py --topic "iphone 17" --once
```

Scheduler outputs:

- `data/latest_batch.json` — latest complete raw batch
- `data/raw_batches.jsonl` — append-only history of raw batches
- `data/metrics_timeseries.csv` — one calculated row per batch
- `data/state.json` — current Metric Engine state

## Replay

Raw data can be processed again without calling YouTube:

```bash
python replay.py --raw-input data/raw_batches.jsonl --metrics-output data/replay_metrics.csv --state-file data/replay_state.json --reset-state
```

This is the intended workflow for testing alternative formulas against the same collected observations.
