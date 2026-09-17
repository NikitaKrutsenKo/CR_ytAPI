# Storage

This package contains the original schema-1 raw JSONL reader/writer, metric CSV writer, validators and dataset builder. Original CLI scripts remain supported.

Desktop schema-2 workspaces are managed by `research/storage.py`. Topic raw bundles use immutable exclusive-create JSON, experiment input manifests carry checksums, and metrics/state are scoped to experiment+topic. See README and `docs/ARCHITECTURE.md` for actual layout and schema compatibility rules.
