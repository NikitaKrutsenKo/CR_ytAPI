# State

`MetricsState` and `StateManager` are the original Gap state and JSON manager, retained for legacy CLI/replay.

The desktop application owns one Gap state, TrendState and TrackedVideoRegistry per experiment/topic. Debug snapshots are written to `experiments/<id>/state/<topic_id>/`. Separate formula experiments never reuse a mutable state file. Replay reconstructs fresh state from immutable raw data; automatic live-process resume is not implemented.
