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
- checkpointed, gzip-compressed TSV export.

This repository is under active research and data-quality review. The software
does not provide a guarantee against reconstruction or proof of compliance with
a legal, policy, or non-consumptive-data standard. Release decisions require
separate institutional review.

## Processing Overview

For each HTID, the pipeline:

1. Resolves the Pairtree ZIP path.
2. Resolves one language for the volume from JSON-LD metadata when available.
3. Uses `langid` as a constrained fallback across the 27 supported languages.
4. Streams the volume into bounded batches of 100 pages by default.
5. Removes recognized scanner boilerplate pages.
6. Examines likely header and footer lines at the beginning and end of a page.
7. Recombines words split by a line-final hyphen.
8. Removes selected zero-width, superscript, and subscript characters.
9. Applies Hebrew-specific diacritic removal where applicable.
10. Bulk-tokenizes up to 100 cleaned pages with the configured Stanza tokenizer
    and, where available, MWT processor.
11. Counts adjacent bigrams within each page without filtering them.
12. Writes the volume `.tsv.gz`, optional result metadata, and run provenance.

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

The primary output is one gzip-compressed volume-level bigram TSV per successfully processed
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
The input may optionally provide a tab-separated language code in column two.
When present, this volume-level language bypasses repeated Bibframe lookup and
text-based detection.

```text
uc1.32106009966604
mdp.39015008170840
# optional comment
```

```text
htid	language
uc1.32106009966604	eng
mdp.39015008170840	eng
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
  --status-dir /path/to/status \
  --gzip-level 1
```

### Arguments

| Argument | Required | Description |
| --- | --- | --- |
| `--pairtree` | Yes | Pairtree root containing source ZIP volumes. |
| `--id-list` | Yes | UTF-8 text file containing one HTID per line. |
| `--bibframe-root` | No | Root containing HTRC Bibframe JSON-LD metadata. If omitted, language resolution uses text fallback. |
| `--output-dir` | Yes | Directory to save volume `.tsv.gz` files and `run_metadata.json`. |
| `--metadata-dir` | Yes | Directory to save global run provenance metadata. |
| `--summary-dir` | No | Enables task-sharded summary CSV files in the specified directory. If omitted, no summary CSV files are written. |
| `--pending-dir` | No | Base directory for task-sharded pending-volume files. Defaults under `--output-dir`. |
| `--status-dir` | No | Directory for compact task-completion records used for campaign reconciliation. |
| `--gzip-level` | No | Gzip compression level from `0` through `9`. Default: `1`. |
| `--flat-output` | No | Writes volume TSVs into one directory instead of a Pairtree-like hierarchy. |
| `--input-is-task-shard` | No | Processes the complete input file because it was assigned specifically to the current SLURM task. |
| `--skip-checkpoints` | No | Skips existing-output checks for a guaranteed-new output directory. Do not use for recovery runs. |

The SLURM wrapper provides deployment defaults through environment variables.
Set `VENV_PATH`, `STANZA_RESOURCES_DIR`, `PAIRTREE_ROOT`, `BIBFRAME_ROOT`,
`ID_LIST`, `SHARD_MANIFEST`, `FRESH_RUN`, `RUN_ROOT`, `OUTPUT_DIR`, `METADATA_DIR`, `PENDING_DIR`,
`RUN_LOG_DIR`, `TASK_LOG_DIR`, `STATUS_DIR`, or `GZIP_LEVEL` before
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

For small compatibility runs, each array task receives a stride-based subset of
`ID_LIST`. Large runs should use task-specific, language-homogeneous shards.
Create shards from a Hathifile whose first two columns are HTID and language:

The production sharder intentionally consumes a separate, preprocessed
two-column TSV (`htid`, `language`). Raw Hathifiles must be reduced to those
columns before this step; the sharder does not infer column positions from the
full Hathifile schema.

For production, use count-balanced sharding without performing one Pairtree
metadata lookup per volume. The supplied BigRed200 job writes a durable
checkpoint every one million input rows and can safely resume:

```bash
mkdir -p logs
sbatch --test-only production_shards.slurm
sbatch production_shards.slurm
```

For a reproducible sample or a small size-balanced experiment, invoke the
sharder directly:

```bash
python3 scripts/create_language_shards.py \
  --input /path/to/hathifile.tsv \
  --output-dir /path/to/sample_shards \
  --limit 5000 \
  --target-volumes 100 \
  --sample-seed 20261021 \
  --pairtree-root /N/project/htrc/data/ht_text
```

`--sample-seed` produces a reproducible random sample instead of taking the
first records in file order. Supported aliases are canonicalized before
sharding. Unsupported languages are retained in compact shards so they appear
in the final pending-volume report rather than disappearing silently.
When `--pairtree-root` is supplied, the script uses a bounded min-heap to
balance each language's shards by compressed ZIP size and records that size in
the shard. Volumes larger than `--isolate-zip-size-mb` are not rejected; they
are placed in one-volume shards so an outlier cannot delay every other volume
in its task. Use `0` to disable this isolation. Missing Pairtree volumes remain
in the input and are reported by the pipeline as `ZIP_NOT_FOUND`. The sharder
also writes `shard_counts.tsv`, allowing campaign preparation to use recorded
counts instead of reopening every shard.

For a small single-array run, submit the generated manifest with a new run root:

```bash
export SHARD_MANIFEST=/path/to/test5_shards/shards_manifest.txt
export RUN_ROOT="outputs/test5_$(date +%Y%m%d_%H%M%S)"
export FRESH_RUN=1
bash submit.sh
```

Large runs use bounded waves so the collection never attempts to submit every
shard as one SLURM array. By default, `submit_campaign.sh` uses lane scheduling:
each finalizer validates its wave and then submits the next wave in that lane.
The first wave writes
one campaign-level provenance manifest; later waves reuse the same run context:

```bash
export SHARD_MANIFEST=/path/to/shards/shards_manifest.txt
export RUN_ROOT="outputs/production_run"
export CAMPAIGN_NAME=initial
export WAVE_SIZE=900
export MAX_CONCURRENT_TASKS=500
export FRESH_RUN=1
bash submit_campaign.sh
```

The submission resource profile is configurable without editing the SLURM
scripts. The current defaults are eight CPUs and 16 GB per array task:

```bash
export TASK_CPUS=8
export TASK_MEMORY=16GB
```

In lane mode, `MAX_CONCURRENT_TASKS` sets the initial array throttle. The campaign stores the
current value in `control/<campaign>/max_concurrent_tasks`, and every later wave
reads that file before submission. To raise or lower concurrency for future
waves without restarting the campaign, replace the file atomically:

```bash
CONTROL_FILE="$RUN_ROOT/control/$CAMPAIGN_NAME/max_concurrent_tasks"
printf '500\n' > "${CONTROL_FILE}.tmp"
mv "${CONTROL_FILE}.tmp" "$CONTROL_FILE"
```

Changing this file does not alter an array that is already running. If the
active array should also change, update its throttle separately with the SLURM
command approved by the cluster team, for example:

```bash
scontrol update JobId=<active-array-job-id> ArrayTaskThrottle=500
```

For a handoff from the existing `rolling` campaign, its
`RUN_ROOT/control/rolling/` directory holds wave plans and submission records,
not bigram output. After deploying the updated code, create a signal there to
let active waves finish without submitting the next wave in either lane:

```bash
export RUN_ROOT=/N/project/htrc/HTRC-Ngrams/outputs/production_20261001
touch "$RUN_ROOT/control/rolling/handoff_to_third_campaign"
```

Retries of an incomplete active wave remain enabled. The signal does not
cancel running jobs or start the third campaign. Wait for both lanes' current
arrays and finalizers to finish, then build the remaining-shard manifest from
the earliest unfinished wave before submitting another campaign. Any wave
submitted before the signal took effect must also be accounted for.

For a later campaign handoff, derive the first unfinished wave from finalized
pending and status reports instead of entering a wave number manually:

```bash
touch "$RUN_ROOT/control/third_campaign/handoff_to_fourth_campaign"
```

The continuous dispatcher checks for `handoff_to_*_campaign` markers during
every loop. The marker stops new wave submissions without cancelling active
arrays or finalizers. Wait for those existing jobs to drain before creating the
next campaign's input:

```bash
python3 scripts/create_remaining_shard_manifest.py \
  --campaign-manifest "$RUN_ROOT/control/third_campaign/waves/campaign_manifest.json" \
  --pending-wave-dir "$RUN_ROOT/pending/campaigns/third_campaign/waves" \
  --status-wave-dir "$RUN_ROOT/status/campaigns/third_campaign/waves" \
  --output-dir "$RUN_ROOT/control/fourth_campaign_input"
```

The command validates every source wave manifest, shard count and record count.
It proves that the finalized prefix plus the generated remaining suffix exactly
cover the source campaign, and writes `handoff_summary.json` with
`coverage_verified: true`. Finalized waves after the first unfinished wave are
intentionally included again so checkpointing can skip their existing outputs
without creating a coverage gap.

For a new campaign, `DISPATCH_MODE=continuous` enables an opt-in controller
that submits another wave as soon as the user's queue has room. It does not
wait for every task in older waves to finish. Continuous arrays have no
per-array `%` throttle. The application-level queue budget prevents the user
from submitting above the configured campaign ceiling, while Slurm still
enforces the cluster's configured node and QOS constraints. The continuous
defaults use 100-task waves, `QUEUE_BUDGET=600` and a maximum live budget of
900 jobs.
That budget is a ceiling on this user's total queued and running jobs,
including unrelated jobs, dispatchers and finalizers; it is not a guarantee of
that many running bigram tasks. Smaller waves allow new work to enter the queue
before an older wave reaches its long tail without exceeding the configured
budget. The controller runs as a one-CPU SLURM job and hands off to a successor
before its one-day limit. It retries incomplete waves with checkpointing and
validates per-wave pending and status reports before marking the campaign
complete.

The dispatcher rereads `control/<campaign-name>/queue_budget` during every
loop. Change the budget atomically without stopping the campaign:

```bash
CONTROL="$RUN_ROOT/control/$CAMPAIGN_NAME/queue_budget"
printf '900\n' > "${CONTROL}.tmp" && mv "${CONTROL}.tmp" "$CONTROL"
```

Use the same command with `600` to restore the normal budget. Values above the
campaign's `MAX_QUEUE_BUDGET` are rejected and the last valid value remains in
use.

```bash
export SHARD_MANIFEST=/path/to/nonoverlapping_remaining_shards/shards_manifest.txt
export RUN_ROOT=/N/project/htrc/HTRC-Ngrams/outputs/production_20261001
export CAMPAIGN_NAME=fourth_campaign
export DISPATCH_MODE=continuous
export CAMPAIGN_LANES=1
export WAVE_SIZE=100
export QUEUE_BUDGET=600
export MAX_QUEUE_BUDGET=900
export FRESH_RUN=0
bash submit_campaign.sh
```

This mode affects only the newly named campaign. Do not submit shards that an
existing campaign is still processing. The new campaign keeps bigrams under
the same `RUN_ROOT/bigrams` and writes its own wave reports under
`RUN_ROOT/pending/campaigns/<campaign-name>/waves` and
`RUN_ROOT/status/campaigns/<campaign-name>/waves`. Its campaign-wide pending
file covers only its own shards; pending reports from earlier campaigns must
be reconciled separately before declaring the whole production collection
complete.

Wave finalizers request two hours. They consolidate the essential pending and
status records before attempting bounded, optional task-log consolidation, so
slow log I/O cannot invalidate an otherwise complete wave.

The controller records submissions in `control/<campaign-name>/submissions.tsv`
and uses `dispatch_intent.json` to stop rather than risk a duplicate submission
if Slurm accepted a job but ledger writing was interrupted. Do not delete an
unresolved intent without checking Slurm and the ledger. A failed or timed-out
controller does not release its `afterok` successor; active wave tasks can
finish, but dispatch must be restarted manually after investigation. To restart a stopped
continuous controller after resolving the cause, submit it from the repository
with its saved configuration:

```bash
export DISPATCH_CONFIG="$RUN_ROOT/control/$CAMPAIGN_NAME/dispatch_config.json"
sbatch --export=ALL,DISPATCH_CONFIG="$DISPATCH_CONFIG" \
  --output="$RUN_ROOT/logs/dispatch_${CAMPAIGN_NAME}_%j.log" \
  dispatch_campaign.slurm
```

For a 30,000-volume rehearsal, a smaller wave size such as `200` exercises
multiple wave transitions while retaining the production control flow.

Each manifest entry becomes one SLURM array task, and that task reads only its
assigned shard. Within each task, `SLURM_CPUS_PER_TASK` determines the maximum
multiprocessing worker count. Smaller shards automatically start fewer workers.
The SLURM wrapper limits OpenMP and common numerical-library thread pools to one
thread per worker to prevent CPU oversubscription.

Monitor a submitted job with the scheduler tools provided by the cluster:

```bash
squeue -u "$USER"
```

The pipeline uses checkpoint validation. An atomically published `.tsv.gz`
with a valid gzip signature and TSV header is treated as complete and skipped.
`FRESH_RUN=1` disables these checks for a guaranteed-new campaign. Recovery and
pending-volume campaigns must use `FRESH_RUN=0`.

## Recovery and Retry

The pipeline is designed to resume after a cancelled job or node failure.
Completed `.tsv.gz` files with a recognized gzip signature and TSV header are
checkpointed and skipped. A missing or invalid output is replaced through the
atomic export path.

Each array task first writes an internal pending-volume shard containing
`htid`, `reason_code`, and `detail`. After the array reaches a terminal state,
the dependent finalization job consolidates the available shards, atomically
writes one final file, and removes the consolidated shards:

```text
<pending-dir>/pending_volumes.tsv
```

For wave campaigns, every wave first writes a pending report under
`pending/campaigns/<campaign-name>/waves/`. After all waves are accounted for,
campaign finalization validates that successful plus pending volumes equals
the selected input count and writes
`pending/campaigns/<campaign-name>/pending_volumes.tsv`. The final file contains
a header even when no volumes remain pending.

Review the reason codes before retrying. Missing source data and unsupported
languages will normally fail again, while corrected permissions, dependencies,
resources, or transient read problems may be retryable. Recover language and
ZIP-size hints from the original shard manifest:

```bash
python3 scripts/create_retry_shards.py \
  --pending-file outputs/production_run/pending/campaigns/initial/pending_volumes.tsv \
  --source-manifest /path/to/original/shards_manifest.txt \
  --output-dir /path/to/retry_shards \
  --target-volumes 100
```

Submit the retry as a new campaign name while preserving the original run root:

```bash
export SHARD_MANIFEST=/path/to/retry_shards/shards_manifest.txt
export RUN_ROOT="outputs/production_run"
export CAMPAIGN_NAME="retry_001"
export FRESH_RUN=0
bash submit_campaign.sh
```

Successful retry outputs are written into the original `bigrams/` Pairtree.
Existing completed volumes are skipped. A retry with changed runtime
configuration must use a new output directory because `run_metadata.json`
intentionally rejects configuration conflicts.

Each retry campaign has its own pending report. Before publishing one pending
file for the collection, reconcile the original and retry outcomes so volumes
that succeeded on retry are removed. Do not process
the same shard simultaneously in separate campaigns. Continuous waves within
one campaign share a run root because each wave owns distinct shards and
writes separate pending and status reports.

For a cancelled array where no configuration changed, resubmitting the
original HTID list is also safe: completed TSVs are checkpointed and only
missing or incomplete outputs are processed again.

## Repository Layout

```text
.
├── run_pipeline.py
├── requirements.txt
├── submit.sh
├── submit_campaign.sh
├── submit_wave.sh
├── dispatch_campaign.slurm
├── submit_bigred.slurm
├── finalize_run.slurm
├── production_shards.slurm
├── scripts/
│   ├── consolidate_logs.py
│   ├── consolidate_pending.py
│   ├── consolidate_status.py
│   ├── dispatch_campaign.py
│   ├── create_language_shards.py
│   ├── create_remaining_shard_manifest.py
│   ├── create_retry_shards.py
│   ├── create_wave_manifests.py
│   └── finalize_campaign.py
└── src/
    ├── config.py
    ├── engine_export.py
    ├── engine_io.py
    ├── engine_nlp.py
    └── provenance.py
```

## Operational Notes

- **Atomic Compressed Writes:** Volume data is written directly to a temporary gzip stream, flushed and synchronized, validated, and atomically renamed to `.tsv.gz`. No uncompressed duplicate is created.
- **Lockless Chunking:** Array tasks do not write concurrently to a shared pending file. Each task writes its own pending shard. A dependent single-process job then atomically replaces `<pending-dir>/pending_volumes.tsv` and removes the completed run's pending shards. Optional result summaries remain task-sharded when enabled.
- **Configuration Fingerprinting:** If you restart a run using an existing `--output-dir`, the script reads `run_metadata.json` from that output directory to verify that the export configuration matches the existing files. A conflicting configuration causes the pipeline to stop rather than mix incompatible outputs.
- **Structured Failure Reporting:** Task-specific pending TSVs contain `htid`, `reason_code`, and `detail`. Stable codes distinguish missing, corrupt, unreadable, and permission-restricted inputs from language, dependency, and tokenizer failures.
- **Task Logging:** Each SLURM array task writes one temporary combined standard-output and standard-error log containing task start, periodic progress, task completion, and fatal errors. After essential pending and status reports are finalized, bounded best-effort log consolidation merges those files into `<run-root>/logs/slurm_<array-job-id>.log`. A slow log merge does not invalidate the wave's data reports. Volume failures and their reason codes are retained in the pending-volume TSV.
- **Page Diagnostics:** Optional task result CSVs record source text pages, readable pages, failed page reads, and Latin-1 decoding fallbacks when `--summary-dir` is supplied.
- **Output Metrics:** Optional task result CSVs distinguish total tokens, exported distinct bigram types, exported bigram occurrences, singleton percentage, and the actual completed TSV size. Storage reporting is based on the exported TSV rather than an estimated serialization format.
- Pairtree ZIPs are streamed in bounded 100-page batches rather than loaded as one volume.
- Bibframe language metadata is resolved before opening source pages. The pipeline reads a text sample for language detection only when usable metadata is unavailable.
- Production runs count bigrams directly and skip singleton, file-size, and other optional summary calculations unless `--summary-dir` is supplied.
- Stanza pipelines are lazily initialized and cached once per worker process.
- Stanza bulk processing reduces per-page pipeline-call overhead while keeping
  page-level token sequences separate.

## Related Documentation

- [`README_EXPORT.md`](README_EXPORT.md): export schema and metadata reference.
- [`limitations.md`](limitations.md): language-by-language data-quality review,
  tokenizer behavior, evidence status, and known interpretation limitations.
