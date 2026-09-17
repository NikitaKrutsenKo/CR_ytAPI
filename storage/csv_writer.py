from __future__ import annotations

import csv
from dataclasses import fields
from pathlib import Path
from typing import Any

from models.metrics import MetricSnapshot


class MetricsCsvWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, snapshot: MetricSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = snapshot.to_dict()
        fieldnames = [field.name for field in fields(MetricSnapshot)]
        write_header = not self.path.exists() or self.path.stat().st_size == 0

        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerow(row)
