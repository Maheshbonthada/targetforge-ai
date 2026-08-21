from __future__ import annotations

import pytest

from targetforge.bioinformatics.fastq_qc import (
    compute_metrics,
    evaluate_pass_fail,
    parse_fastq_records,
)

_GOOD_FASTQ = (
    "@read1\nACGTACGTACGTACGTACGTACGTACGTAC\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
    "@read2\nTGCATGCATGCATGCATGCATGCATGCATG\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
)

_ADAPTER_FASTQ = "@read1\nGGCCAGATCGGAAGAGCACACGTCTGAACT\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"

_LOW_QUAL_FASTQ = "@read1\nACGTACGTACGTACGTACGTACGTACGTAC\n+\n##############################\n"


class TestParsing:
    def test_parses_valid_records(self):
        records = parse_fastq_records(_GOOD_FASTQ)
        assert len(records) == 2
        assert records[0][0] == "ACGTACGTACGTACGTACGTACGTACGTAC"

    def test_rejects_record_count_not_multiple_of_four(self):
        with pytest.raises(ValueError, match="multiple of 4"):
            parse_fastq_records("@only\nACGT\n+\n")

    def test_rejects_header_not_starting_with_at(self):
        with pytest.raises(ValueError, match="does not start with '@'"):
            parse_fastq_records("NOTHEADER\nACGT\n+\nIIII\n")

    def test_rejects_sequence_quality_length_mismatch(self):
        with pytest.raises(ValueError, match="length mismatch"):
            parse_fastq_records("@read1\nACGT\n+\nII\n")


class TestMetrics:
    def test_empty_records_yields_zeroed_metrics(self):
        metrics = compute_metrics([])
        assert metrics.total_reads == 0
        assert metrics.q30_rate == 0.0

    def test_gc_content_computed_correctly(self):
        # 30 bases total, 15 G/C (ACGT repeated) = 50%
        metrics = compute_metrics(parse_fastq_records(_GOOD_FASTQ))
        assert metrics.gc_content == pytest.approx(0.5, abs=0.01)

    def test_high_quality_reads_have_q30_rate_near_one(self):
        metrics = compute_metrics(parse_fastq_records(_GOOD_FASTQ))
        assert metrics.q30_rate > 0.95

    def test_low_quality_reads_have_q30_rate_near_zero(self):
        metrics = compute_metrics(parse_fastq_records(_LOW_QUAL_FASTQ))
        assert metrics.q30_rate == 0.0

    def test_adapter_detected_when_present(self):
        metrics = compute_metrics(parse_fastq_records(_ADAPTER_FASTQ))
        assert metrics.adapter_contamination_pct == 100.0

    def test_no_adapter_when_absent(self):
        metrics = compute_metrics(parse_fastq_records(_GOOD_FASTQ))
        assert metrics.adapter_contamination_pct == 0.0


class TestPassFail:
    def test_clean_sample_passes(self):
        metrics = compute_metrics(parse_fastq_records(_GOOD_FASTQ))
        passed, flags = evaluate_pass_fail(metrics)
        assert passed is True
        assert flags == []

    def test_low_qual_sample_fails_with_flag(self):
        metrics = compute_metrics(parse_fastq_records(_LOW_QUAL_FASTQ))
        passed, flags = evaluate_pass_fail(metrics)
        assert passed is False
        assert any("Q30" in f for f in flags)

    def test_adapter_contaminated_sample_fails_with_flag(self):
        metrics = compute_metrics(parse_fastq_records(_ADAPTER_FASTQ))
        passed, flags = evaluate_pass_fail(metrics)
        assert passed is False
        assert any("adapter" in f.lower() for f in flags)
