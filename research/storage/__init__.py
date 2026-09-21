"""Storage package: filesystem workspaces, JSON/JSONL I/O, state persistence, and validation."""

from research.storage.csv_writer import MetricsCsvWriter
from research.storage.dataset_builder import DatasetBuilder
from research.storage.io import (
    append_jsonl,
    cleanup_temporary_files,
    iter_jsonl,
    read_json,
    read_jsonl,
    write_json,
)
from research.storage.raw_batch_store import (
    RawBatchStore,
    batch_from_dict,
)
from research.storage.state import (
    MetricsState,
    NormalizationBoundaryState,
    StateManager,
)
from research.storage.validator import (
    ValidationError,
    validate_batch,
    validate_metrics_csv,
    validate_snapshot,
)
from research.storage.workspace import TopicWorkspaceManager

__all__ = [
    "DatasetBuilder",
    "MetricsCsvWriter",
    "MetricsState",
    "NormalizationBoundaryState",
    "RawBatchStore",
    "StateManager",
    "TopicWorkspaceManager",
    "ValidationError",
    "append_jsonl",
    "batch_from_dict",
    "cleanup_temporary_files",
    "iter_jsonl",
    "read_json",
    "read_jsonl",
    "validate_batch",
    "validate_metrics_csv",
    "validate_snapshot",
    "write_json",
]
