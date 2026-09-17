from storage.dataset_builder import DatasetBuilder


def test_dataset_builder_adds_future_targets(tmp_path):
    source = tmp_path / "metrics.csv"
    output = tmp_path / "dataset.csv"
    source.write_text(
        "timestamp,topic,gap_score,pr_batch\n"
        "2026-09-14T09:00:00Z,x,0.1,1.0\n"
        "2026-09-14T09:30:00Z,x,0.2,1.2\n"
        "2026-09-14T10:00:00Z,x,0.3,1.5\n",
        encoding="utf-8",
    )

    count = DatasetBuilder().build(
        source, output, [("gap_score", 1), ("pr_batch", 2)]
    )
    assert count == 3
    text = output.read_text(encoding="utf-8")
    assert "target_gap_score_h1" in text
    assert "target_pr_batch_h2" in text
    assert "0.2,1.5" in text
