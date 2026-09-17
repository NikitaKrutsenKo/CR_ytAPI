# Metrics

`MetricEngine` and `MetricConfig` are the original Gap implementation, retained unchanged for compatibility. They accept YouTubeBatch and independent MetricsState and do not make HTTP requests.

The desktop application names this engine `research.gap.GapEngine`. Independent YouTube Trend research lives in `research/trend.py`. See `docs/FORMULAS.md` for exact lineage, missing-data semantics and the distinction between the YouTube research score and unavailable production multi-platform Trend Score.
