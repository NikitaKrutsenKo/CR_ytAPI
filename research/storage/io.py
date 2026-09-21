"""Strict JSON and JSON Lines file operations with atomic writing, compression, and checksumming."""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from research.core.domain import encode


def write_json(
    path: Path,
    value,
    exclusive: bool = False,
    indent: int | None = 2,
    compact: bool = False,
) -> None:
    """Write strict JSON atomically; immutable records use exclusive creation.

    Args:
        path: Target file path (supports .json or .json.gz).
        value: Serializable data structure.
        exclusive: If True, fails if the target file already exists ('x' mode).
        indent: Indentation level for human-friendly formatting (default 2).
        compact: If True or if indent is None, uses compact single-line separators.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact or indent is None:
        text = json.dumps(encode(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    else:
        text = json.dumps(encode(value), ensure_ascii=False, indent=indent, allow_nan=False)

    is_gz = path.suffix == ".gz"
    if exclusive:
        if is_gz:
            with path.open("xb") as handle:
                handle.write(gzip.compress(text.encode("utf-8")))
        else:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(text)
    else:
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            if is_gz:
                temporary.write_bytes(gzip.compress(text.encode("utf-8")))
            else:
                temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass


def read_json(path: Path):
    """Read and parse a JSON or Gzip-compressed JSON file with UTF-8/BOM support."""
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                return json.load(handle)
        with path.open("rb") as handle:
            magic = handle.read(2)
        if magic == b"\x1f\x8b":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                return json.load(handle)
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"Cannot read JSON file: {path}") from exc


def append_jsonl(path: Path, value) -> None:
    """Append a single JSON record as a line to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(encode(value), ensure_ascii=False, allow_nan=False) + "\n")


def iter_jsonl(path: Path) -> Iterator[dict]:
    """Stream records from a JSONL file line-by-line without buffering the entire file in memory."""
    if not path.exists():
        raise ValueError(f"Dataset missing: {path}")
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for number, line in enumerate(handle, 1):
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except ValueError as exc:
                        raise ValueError(f"Corrupt dataset {path.name}, line {number}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot read dataset: {path}") from exc


def read_jsonl(path: Path) -> list[dict]:
    """Read all records from a JSONL file into a list of dictionaries."""
    return list(iter_jsonl(path))


def cleanup_temporary_files(root: Path) -> int:
    """Scan data and experiments directories to safely remove orphaned .tmp files."""
    removed = 0
    for base in (root / "data", root / "experiments"):
        if base.exists():
            for tmp_path in base.rglob("*.tmp"):
                try:
                    tmp_path.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed
