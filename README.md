# HTRC N-grams Pipeline

The HTRC N-grams Pipeline extracts volume-level bigram frequency data from
HathiTrust plain-text Pairtree volumes. It is designed for research computing
environments where large collections are processed with multiple workers or
SLURM array jobs.

The pipeline combines:

- volume-level language resolution;
- multilingual tokenization with Stanford Stanza;
- page-level text cleanup;
- direct, unfiltered bigram counting;
- run-level provenance metadata; and
- checkpointed TSV export.

This repository is under active research and data-quality review. The software
does not provide a guarantee against reconstruction or proof of compliance with
a legal, policy, or non-consumptive-data standard. Release decisions require
separate institutional review.

## Processing Overview

For each HTID, the pipeline:

1. Resolves the Pairtree ZIP path.
2. Resolves one language for the volume from JSON-LD metadata when available.
3. Uses `langid` as a constrained fallback across the 27 supported languages.
4. Streams the volume into bounded batches of 50 pages.
5. Removes recognized scanner boilerplate pages.
6. Examines likely header and footer lines at the beginning and end of a page.
7. Recombines words split by a line-final hyphen.
8. Removes selected zero-width, superscript, and subscript characters.
9. Applies Hebrew-specific diacritic removal where applicable.
10. Bulk-tokenizes up to 50 cleaned pages with the configured Stanza tokenizer
    and, where available, MWT processor.
11. Counts adjacent bigrams within each page without filtering them.
12. Writes the volume TSV, optional result metadata, and run provenance.

Stanza returns one document for each page in the batch. Pages remain
independent, and bigrams do not cross page boundaries.

## Supported Languages

The pipeline uses three-letter language-profile codes internally. Source
metadata codes are normalized to these configured profiles when necessary.
The aliases `chi`/`zho`, `ger`/`deu`, `fre`/`fra`, `ces`/`cze`, and
`scr`/`hrv` resolve to one canonical internal profile. A metadata language
outside the configured profiles is recorded with reason code
`UNSUPPORTED_LANGUAGE`; the unsupported code is retained in the detail field
instead of being routed through a generic Latin fallback.

| Language | Profile code | Stanza code |
| --- | --- | --- |
| English | `eng` | `en` |
| German | `deu` | `de` |
| French | `fra` | `fr` |
| Spanish | `spa` | `es` |
| Chinese | `zho` | `zh-hans` |
| Japanese | `jpn` | `ja` |
| Russian | `rus` | `ru` |
| Italian | `ita` | `it` |
| Portuguese | `por` | `pt` |
| Arabic | `ara` | `ar` |
| Latin | `lat` | `la` |
| Dutch | `dut` | `nl` |
| Polish | `pol` | `pl` |
| Swedish | `swe` | `sv` |
| Hebrew | `heb` | `he` |
| Korean | `kor` | `ko` |
| Czech | `cze` | `cs` |
| Danish | `dan` | `da` |
| Indonesian | `ind` | `id` |
| Hungarian | `hun` | `hu` |
| Hindi | `hin` | `hi` |
| Croatian | `hrv` | `hr` |
| Turkish | `tur` | `tr` |
| Norwegian | `nor` | `no` (Bokmål alias) |
| Thai | `tha` | `th` |
| Vietnamese | `vie` | `vi` |
| Urdu | `urd` | `ur` |

The aliases `ger`, `fre`, `chi`, `ces`, and `scr` are normalized to the
corresponding configured profiles.

## Tokenization

Stanza performs language-specific tokenization. The pipeline preserves:

- capitalization;
- numbers;
- punctuation tokens; and
- Stanza multi-word-token expansions.

For example:

```text
The well-known library doesn't close.
```

produces tokens similar to:

```text
The | well | - | known | library | does | n't | close | .
```

Inline hyphens are separated before Stanza tokenization. A physical line-break
hyphen is treated differently:

```text
inter-
national
```

is normalized to:

```text
international
```

Tokenizer behavior is model-specific. Known model-specific behavior is
documented in [`limitations.md`](limitations.md).

## Generated Files

The primary output is one volume-level bigram TSV per successfully processed
HTID. Operational deployments also create provenance manifests,
pending-volume records, and scheduler logs. Optional task-sharded result
summaries are written only when `--summary-dir` is supplied. These supporting
files are intended for pipeline operation and review and do not all need to be
distributed with the research dataset.

See [`README_EXPORT.md`](README_EXPORT.md) for the files and fields that may be
provided to researchers.

## Requirements

- Python 3
- Access to HathiTrust plain-text Pairtree ZIP volumes
- Stanza tokenizer models for the target languages
- Sufficient memory and temporary storage for the selected worker count

Install Python dependencies in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

Set the Stanza model directory:

```bash
export STANZA_RESOURCES_DIR=/path/to/stanza_resources
```

Download the configured tokenizer and MWT resources before submitting large
jobs:

```bash
python3 - <<'PY'
import os
import stanza

from src.engine_nlp import STANZA_LANG_MAP, STANZA_MWT_LANGS

model_dir = os.environ["STANZA_RESOURCES_DIR"]
for stanza_code in sorted(set(STANZA_LANG_MAP.values())):
    processors = (
        "tokenize,mwt"
        if stanza_code in STANZA_MWT_LANGS
        else "tokenize"
    )
    stanza.download(
        stanza_code,
        model_dir=model_dir,
        processors=processors,
        verbose=False,
    )
PY
```

Compute nodes should not be expected to download models during processing.

## Inputs

### HTID List

Provide one HTID per line. Empty lines and lines beginning with `#` are ignored.

```text
uc1.32106009966604
mdp.39015008170840
# optional comment
```

### Pairtree Root

`--pairtree` must point to the root containing provider-specific Pairtree
directories.

### Language Metadata

The deployment may provide HTRC JSON-LD bibliographic metadata through
`--bibframe-root`. When a language value is available, it is preferred over
statistical fallback detection. If the argument is omitted, the resolver uses
the text-based fallback. Short volumes use all available sampled pages. Long
volumes use interior pages 40 through 59, with an all-page retry if that window
is blank. Language assignment is volume-level rather than page-level.

## Running the Pipeline

The main entry point is `run_pipeline.py`.

```bash
python3 run_pipeline.py \
  --pairtree /path/to/ht_text \
  --id-list /path/to/htids.txt \
  --bibframe-root /path/to/bibframe-jsonld \
  --output-dir /path/to/output \
  --metadata-dir /path/to/metadata \
  --pending-dir /path/to/pending \
  --max-zip-size-mb 150
```

### Arguments

| Argument | Required | Description |
| --- | --- | --- |
| `--pairtree` | Yes | Pairtree root containing source ZIP volumes. |
| `--id-list` | Yes | UTF-8 text file containing one HTID per line. |
| `--bibframe-root` | No | Root containing HTRC Bibframe JSON-LD metadata. If omitted, language resolution uses text fallback. |
| `--output-dir` | Yes | Directory to save volume TSVs and `run_metadata.json`. |
| `--metadata-dir` | Yes | Directory to save global run provenance metadata. |
| `--summary-dir` | No | Enables task-sharded summary CSV files in the specified directory. If omitted, no summary CSV files are written. |
| `--pending-dir` | No | Base directory for task-sharded pending-volume files. Defaults under `--output-dir`. |
| `--max-zip-size-mb` | No | Skip source ZIPs larger than this many MiB. Default: `150`. |
| `--flat-output` | No | Writes volume TSVs into one directory instead of a Pairtree-like hierarchy. |

The SLURM wrapper provides deployment defaults through environment variables.
Set `VENV_PATH`, `STANZA_RESOURCES_DIR`, `PAIRTREE_ROOT`, `BIBFRAME_ROOT`,
`ID_LIST`, `RUN_ROOT`, `OUTPUT_DIR`, `METADATA_DIR`, `PENDING_DIR`,
`RUN_LOG_DIR`, `TASK_LOG_DIR`, or
`MAX_ZIP_SIZE_MB` before
submission to override a default without editing the script.

## SLURM Execution

`submit.sh` counts nonempty HTIDs and submits a SLURM array through
`submit_bigred.slurm`. It also submits `finalize_run.slurm` with an `afterany`
dependency. After the array reaches a terminal state, the finalizer merges the
pending-volume shards, consolidates the temporary task logs into one run log,
and removes the temporary files.

```bash
bash submit.sh
```

Each array task receives a stride-based subset of the HTID list. Within each
task, `SLURM_CPUS_PER_TASK` determines the multiprocessing worker count.

Monitor a submitted job with the scheduler tools provided by the cluster:

```bash
squeue -u "$USER"
```

The pipeline uses checkpoint validation: if a target TSV already exists and
possesses both a valid header and a final newline byte, that volume is treated
as complete and skipped.

## Recovery and Retry

The pipeline is designed to resume after a cancelled job or node failure.
Completed TSVs with a recognized header and final newline are checkpointed and
skipped. An existing incomplete TSV is replaced through the atomic export path.

Each array task first writes an internal pending-volume shard containing
`htid`, `reason_code`, and `detail`. After the array reaches a terminal state,
the dependent finalization job consolidates the available shards, atomically
writes one final file, and removes the consolidated shards:

```text
<pending-dir>/pending_volumes.tsv
```

The final file contains a header even when no volumes remain pending. It lists
volume-level failures reported by completed tasks. If the finalization job
fails, inspect its `finalize_<job-id>.log` before retrying consolidation.

Review the reason codes before retrying. Missing source data and unsupported
languages will normally fail again, while corrected permissions, dependencies,
resources, or transient read problems may be retryable. Create a one-HTID-per-
line retry list from the final file:

```bash
awk -F '\t' 'NR > 1 {print $1}' \
  outputs/current_run/pending/pending_volumes.tsv \
  > htids_retry.txt
```

Inspect `reason_code` and `detail`, correct the underlying problem, then submit
`htids_retry.txt` with the same output directory:

```bash
ID_LIST=htids_retry.txt bash submit.sh
```

Existing completed volumes will be skipped. A retry with changed runtime
configuration must use a new output directory because `run_metadata.json`
intentionally rejects configuration conflicts.

When the retry uses the same `PENDING_DIR`, its consolidation job replaces
`pending_volumes.tsv` with only the volumes that still fail. Volumes that
succeed on retry therefore disappear from the final pending file. Do not run
overlapping submissions against the same `RUN_ROOT`; use a separate run root
for concurrent submissions.

For a cancelled array where no configuration changed, resubmitting the
original HTID list is also safe: completed TSVs are checkpointed and only
missing or incomplete outputs are processed again.

## Repository Layout

```text
.
├── run_pipeline.py
├── requirements.txt
├── submit.sh
├── submit_bigred.slurm
├── finalize_run.slurm
├── scripts/
│   ├── consolidate_logs.py
│   └── consolidate_pending.py
└── src/
    ├── config.py
    ├── engine_export.py
    ├── engine_io.py
    ├── engine_nlp.py
    └── provenance.py
```

## Operational Notes

- **Atomic Writes:** Volume TSVs are written to a `.tmp` file first and then atomically renamed to `.tsv` to reduce the risk of data corruption even if a node crashes mid-write.
- **Lockless Chunking:** Array tasks do not write concurrently to a shared pending file. Each task writes its own pending shard. A dependent single-process job then atomically replaces `<pending-dir>/pending_volumes.tsv` and removes the completed run's pending shards. Optional result summaries remain task-sharded when enabled.
- **Configuration Fingerprinting:** If you restart a run using an existing `--output-dir`, the script reads `run_metadata.json` from that output directory to verify that the export configuration matches the existing files. A conflicting configuration causes the pipeline to stop rather than mix incompatible outputs.
- **Structured Failure Reporting:** Task-specific pending TSVs contain `htid`, `reason_code`, and `detail`. Stable codes distinguish missing, corrupt, unreadable, oversized, and permission-restricted inputs from language, dependency, and tokenizer failures.
- **Task Logging:** Each SLURM array task writes one temporary combined standard-output and standard-error log containing task start, periodic progress, task completion, and fatal errors. A dependent finalization job merges those files into `<run-root>/logs/slurm_<array-job-id>.log` and removes the temporary task logs. Volume failures and their reason codes are retained in the pending-volume TSV.
- **Page Diagnostics:** Optional task result CSVs record source text pages, readable pages, failed page reads, and Latin-1 decoding fallbacks when `--summary-dir` is supplied.
- **Output Metrics:** Optional task result CSVs distinguish total tokens, exported distinct bigram types, exported bigram occurrences, singleton percentage, and the actual completed TSV size. Storage reporting is based on the exported TSV rather than an estimated serialization format.
- Pairtree ZIPs are streamed in bounded 50-page batches rather than loaded as one volume.
- Bibframe language metadata is resolved before opening source pages. The pipeline reads a text sample for language detection only when usable metadata is unavailable.
- Production runs count bigrams directly and skip singleton, file-size, and other optional summary calculations unless `--summary-dir` is supplied.
- Stanza pipelines are lazily initialized and cached once per worker process.
- Stanza bulk processing reduces per-page pipeline-call overhead while keeping
  page-level token sequences separate.
- The configured ZIP-size guard skips compressed input files above the deployment threshold.

## Related Documentation

- [`README_EXPORT.md`](README_EXPORT.md): export schema and metadata reference.
- [`limitations.md`](limitations.md): language-by-language data-quality review,
  tokenizer behavior, evidence status, and known interpretation limitations.
