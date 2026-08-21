"""Real FASTQ quality-control metrics — no external binary required.

This deliberately reimplements the small slice of what FastQC computes
(Q30 rate, GC content, adapter contamination, read-length distribution)
rather than shelling out to the FastQC jar, because those metrics are simple
enough to compute directly and doing so means the QC Agent (1) is testable
with plain Python and a tiny synthetic FASTQ file — no container, no binary,
no reference data. For a full FastQC HTML report (useful for a human to
eyeball), see docs/scaling-to-real-genomes.md — that's a real containerized
tool call in production, this module is not trying to replace it, only the
numeric checks the QC Agent needs to make a pass/fail call.
"""

from __future__ import annotations

from dataclasses import dataclass

# Illumina TruSeq universal adapter — the single most common contamination
# source in short-read sequencing. A production QC agent would check a small
# library of known adapters (Nextera, small RNA, etc.); this is the
# highest-value one to check first.
_ADAPTER_SEQUENCES = ("AGATCGGAAGAGC",)

_PHRED33_OFFSET = 33
_Q30_THRESHOLD = 30


@dataclass
class FastqMetrics:
    total_reads: int
    mean_read_length: float
    gc_content: float
    q30_rate: float
    adapter_contamination_pct: float


def parse_fastq_records(fastq_text: str) -> list[tuple[str, str]]:
    """Returns (sequence, quality_string) pairs. Raises ValueError on a
    malformed file (wrong line count, header not starting with '@') rather
    than silently skipping bad records — a QC agent that swallows a malformed
    input file defeats the purpose of QC.
    """
    lines = [line for line in fastq_text.splitlines() if line != ""]
    if len(lines) % 4 != 0:
        raise ValueError(f"FASTQ record count not a multiple of 4 (got {len(lines)} lines)")

    records: list[tuple[str, str]] = []
    for i in range(0, len(lines), 4):
        header, sequence, plus_line, quality = lines[i : i + 4]
        if not header.startswith("@"):
            raise ValueError(f"Malformed FASTQ: line {i + 1} does not start with '@'")
        if not plus_line.startswith("+"):
            raise ValueError(f"Malformed FASTQ: line {i + 3} does not start with '+'")
        if len(sequence) != len(quality):
            raise ValueError(f"Malformed FASTQ: sequence/quality length mismatch at record {i // 4}")
        records.append((sequence, quality))
    return records


def compute_metrics(records: list[tuple[str, str]]) -> FastqMetrics:
    if not records:
        return FastqMetrics(0, 0.0, 0.0, 0.0, 0.0)

    total_bases = 0
    gc_bases = 0
    q30_bases = 0
    reads_with_adapter = 0
    total_length = 0

    for sequence, quality in records:
        total_length += len(sequence)
        total_bases += len(sequence)
        gc_bases += sum(1 for base in sequence if base in "GCgc")
        q30_bases += sum(1 for ch in quality if (ord(ch) - _PHRED33_OFFSET) >= _Q30_THRESHOLD)
        if any(adapter in sequence for adapter in _ADAPTER_SEQUENCES):
            reads_with_adapter += 1

    n = len(records)
    return FastqMetrics(
        total_reads=n,
        mean_read_length=total_length / n,
        gc_content=gc_bases / total_bases if total_bases else 0.0,
        q30_rate=q30_bases / total_bases if total_bases else 0.0,
        adapter_contamination_pct=100.0 * reads_with_adapter / n,
    )


# Thresholds a real QC pipeline (e.g. FastQC's own defaults) uses to flag a
# sample. Deterministic, not an LLM judgment — see docs/agents.md, Agent 1.
def evaluate_pass_fail(metrics: FastqMetrics) -> tuple[bool, list[str]]:
    flags: list[str] = []
    if metrics.q30_rate < 0.80:
        flags.append(f"Low Q30 rate ({metrics.q30_rate:.1%}, expected >= 80%)")
    if metrics.adapter_contamination_pct > 10.0:
        flags.append(f"High adapter contamination ({metrics.adapter_contamination_pct:.1f}% of reads)")
    if metrics.mean_read_length < 30:
        flags.append(f"Very short mean read length ({metrics.mean_read_length:.0f}bp)")
    if not (0.30 <= metrics.gc_content <= 0.65):
        flags.append(f"GC content outside typical human range ({metrics.gc_content:.1%})")
    return len(flags) == 0, flags
