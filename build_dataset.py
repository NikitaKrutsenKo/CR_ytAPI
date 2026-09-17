from __future__ import annotations

import argparse

from storage.dataset_builder import DatasetBuilder


def main() -> None:
    parser = argparse.ArgumentParser(description="Build CR:Correlator dataset from metrics CSV")
    parser.add_argument("--input", default="data/metrics_timeseries.csv")
    parser.add_argument("--output", default="data/correlator_dataset.csv")
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="Target specification metric:H, repeatable, e.g. gap_score:12",
    )
    args = parser.parse_args()

    specs: list[tuple[str, int]] = []
    for raw in args.target:
        try:
            metric, raw_h = raw.rsplit(":", 1)
            specs.append((metric, int(raw_h)))
        except ValueError as exc:
            raise SystemExit(f"Invalid --target '{raw}'. Use metric:H") from exc

    count = DatasetBuilder().build(args.input, args.output, specs)
    print(f"Built {count} rows into {args.output}")


if __name__ == "__main__":
    main()
