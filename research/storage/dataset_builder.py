"""Dataset transformation builder for Correlator forecasting models."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path


class DatasetBuilder:
    """Builds a Correlator-ready dataset and optional future prediction targets from metrics timeseries."""

    def build(
        self,
        input_csv: str | Path,
        output_csv: str | Path,
        target_specs: Iterable[tuple[str, int]] = (),
    ) -> int:
        """Read metrics CSV, compute future shifted targets, and write dataset CSV.

        Args:
            input_csv: Source CSV path containing metrics timeseries.
            output_csv: Destination CSV path.
            target_specs: Iterable of (metric_name, horizon_steps) tuples.

        Returns:
            Number of rows written to output dataset.
        """
        input_csv = Path(input_csv)
        output_csv = Path(output_csv)

        with input_csv.open("r", newline="", encoding="utf-8") as src:
            rows = list(csv.DictReader(src))
            if not rows:
                raise ValueError("Input metrics CSV is empty")

        fields = list(rows[0].keys())
        specs = list(target_specs)
        for metric, horizon in specs:
            if metric not in fields:
                raise ValueError(f"Unknown target metric: {metric}")
            if horizon <= 0:
                raise ValueError("Target horizon must be positive")

        for metric, horizon in specs:
            column = f"target_{metric}_h{horizon}"
            fields.append(column)
            for index, row in enumerate(rows):
                future_index = index + horizon
                row[column] = rows[future_index][metric] if future_index < len(rows) else ""

        output_csv.parent.mkdir(parents=True, exist_ok=True)
        with output_csv.open("w", newline="", encoding="utf-8") as dst:
            writer = csv.DictWriter(dst, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

        return len(rows)
