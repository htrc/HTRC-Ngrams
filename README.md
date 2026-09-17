# HTRC N-grams Pipeline

The HTRC N-grams Pipeline extracts volume-level bigram frequency data from
HathiTrust plain-text Pairtree volumes. It is designed for research computing
environments where large collections are processed with multiple workers or
SLURM array jobs.

The pipeline combines:

- volume-level language resolution;
- multilingual tokenization with Stanford Stanza;
- page-level text cleanup;
- unigram and bigram counting;
- three runtime-selectable filtering operations;
- per-volume stopword audit files;
- run-level provenance metadata; and
- checkpointed TSV export.

This repository is under active research and data-quality review. Filtering
changes the content and size of an export, but no filter or filter combination
in this software should be interpreted as a guarantee against reconstruction
or as proof of compliance with a legal, policy, or non-consumptive-data
standard. Release decisions require separate institutional review.

## Processing Overview

For each HTID, the pipeline:

1. Resolves the Pairtree ZIP path.
2. Resolves one language for the volume from JSON-LD metadata when available.
3. Uses `langid` as a constrained fallback across the 27 supported languages.
4. Streams the volume one page at a time.
5. Removes recognized scanner boilerplate pages.
6. Examines likely header and footer lines at the beginning and end of a page.
7. Recombines words split by a line-final hyphen.
8. Removes selected zero-width, superscript, and subscript characters.
9. Applies Hebrew-specific diacritic removal where applicable.
10. Tokenizes the page with the configured Stanza tokenizer and, where
    available, MWT processor.
11. Counts case-sensitive unigrams and adjacent bigrams within that page.
12. Applies the configured filters.
13. Writes the volume TSV, stopword audit files, result metadata, and run
    provenance.

Pages are processed independently. Bigrams do not cross page boundaries.

## Supported Languages

The pipeline uses three-letter language-profile codes internally. Source
metadata codes are normalized to these configured profiles when necessary.
The aliases `chi`/`zho`, `ger`/`deu`, `fre`/`fra`, and `ces`/`cze` resolve to
one canonical internal profile. A metadata language outside the configured
profiles is recorded with reason code `UNSUPPORTED_LANGUAGE`; the unsupported
code is retained in the detail field instead of being routed through a generic
Latin fallback.

| Language | Profile code | Stanza code | Stopword source |
| --- | --- | --- | --- |
| English | `eng` | `en` | NLTK |
| German | `deu` | `de` | NLTK |
| French | `fra` | `fr` | NLTK |
| Spanish | `spa` | `es` | NLTK |
| Chinese | `zho` | `zh-hans` | NLTK |
| Japanese | `jpn` | `ja` | stopwords-iso |
| Russian | `rus` | `ru` | NLTK |
| Italian | `ita` | `it` | NLTK |
| Portuguese | `por` | `pt` | NLTK |
| Arabic | `ara` | `ar` | NLTK |
| Latin | `lat` | `la` | stopwords-iso |
| Dutch | `dut` | `nl` | NLTK |
| Polish | `pol` | `pl` | stopwords-iso |
| Swedish | `swe` | `sv` | NLTK |
| Hebrew | `heb` | `he` | NLTK |
| Korean | `kor` | `ko` | stopwords-iso |
| Czech | `cze` | `cs` | stopwords-iso |
| Danish | `dan` | `da` | NLTK |
| Indonesian | `ind` | `id` | NLTK |
| Hungarian | `hun` | `hu` | NLTK |
| Hindi | `hin` | `hi` | stopwords-iso |
| Croatian | `hrv` | `hr` | stopwords-iso |
| Turkish | `tur` | `tr` | NLTK |
| Norwegian | `nor` | `no` (Bokmål alias) | NLTK |
| Thai | `tha` | `th` | stopwords-iso |
| Vietnamese | `vie` | `vi` | stopwords-iso |
| Urdu | `urd` | `ur` | stopwords-iso |

The aliases `ger`, `fre`, `chi`, and `ces` are normalized to the corresponding
configured profiles.

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

## Filtering

The execution path provides three selectable filters. Their enabled states and
thresholds are recorded in `run_metadata.json` and run provenance so that an
export can be interpreted and reproduced.

### Filter Policy Status

The current release-candidate policy applies Filters 1, 2, and 3 in that order,
using `min_freq=2` and `min_pmi=0.0`. The command-line defaults and BigRed200
wrapper implement that combination. This is the repository's working policy
while final red-team confirmation is pending; if the decision changes, the
filter switches, metadata, and documentation must be updated together.

Filter 4 remains an experimental helper in `engine_filters.py`, but the current
`run_pipeline.py` execution path does not call it. Consequently,
`--max-exposure` is currently a recorded compatibility parameter and does not
alter exported rows. It is not part of the release-candidate policy and must
not be described as an active protection.

### Filter 1: Standard Stopword Bigrams

For each volume, the pipeline:

1. loads a public language-specific stopword list;
2. intersects that list with the volume's case-folded unigram counts;
3. selects up to the 25 most frequent matching stopwords; and
4. removes a bigram only when both tokens belong to that selected set.

Proper nouns and other high-frequency content words are not selected unless
they appear in the public stopword source.

### Filter 2: Minimum Frequency

Removes a bigram type when its volume-level frequency is below `min_freq`.

```text
frequency >= min_freq
```

The command-line default is `2`.

### Filter 3: Pointwise Mutual Information

Calculates volume-level pointwise mutual information:

```text
PMI(w1, w2) = log2(P(w1, w2) / (P(w1) * P(w2)))
```

The calculation uses the original volume-level unigram counts, token total, and
total bigram occurrences. Earlier filtering therefore does not redefine the
base probabilities. Rows below `min_pmi` are removed. The command-line default
is `0.0`.

PMI is sensitive to low-frequency pairs and should be interpreted together with
frequency.

### Experimental Filter 4: Exposure Cap

Limits retained bigram occurrences relative to the original volume token count.
The implementation computes:

```text
maximum bigram occurrences = floor(max_exposure * total_tokens / 2)
```

The helper would retain rows in ranked order until adding another row exceeded
the occurrence budget. It is not active in the current execution path. A final
team decision is required before it is either integrated or removed.

## Generated Files

The primary output is one volume-level bigram TSV per successfully processed
HTID. Operational deployments can also create internal result summaries,
provenance manifests, stopword audit files, pending-volume records, and
scheduler logs. These supporting files are intended for pipeline operation and
review and do not all need to be distributed with the research dataset.

See [`README_EXPORT.md`](README_EXPORT.md) for the files and fields that may be
provided to researchers.

## Requirements

- Python 3
- Access to HathiTrust plain-text Pairtree ZIP volumes
- Stanza tokenizer models for the target languages
- NLTK stopword corpus
- Sufficient memory and temporary storage for the selected worker count

Install Python dependencies in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

Install the NLTK stopword corpus into a persistent location:

```bash
export NLTK_DATA=/path/to/nltk_data
python3 -m nltk.downloader -d "$NLTK_DATA" stopwords
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
  --audit-dir /path/to/audit \
  --metadata-dir /path/to/metadata \
  --summary-dir /path/to/summaries \
  --pending-dir /path/to/pending \
  --max-zip-size-mb 150 \
  --min-freq 2 \
  --min-pmi 0.0 \
  --filter1 \
  --filter2 \
  --filter3
```

### Arguments

| Argument | Required | Description |
| --- | --- | --- |
| `--pairtree` | Yes | Pairtree root containing source ZIP volumes. |
| `--id-list` | Yes | UTF-8 text file containing one HTID per line. |
| `--bibframe-root` | No | Root containing HTRC Bibframe JSON-LD metadata. If omitted, language resolution uses text fallback. |
| `--output-dir` | Yes | Directory to save volume TSVs and `run_metadata.json`. |
| `--audit-dir` | Yes | Directory to save audit files (e.g., dropped bigrams). |
| `--metadata-dir` | Yes | Directory to save global run provenance metadata. |
| `--summary-dir` | No | Base directory for task-sharded summary CSV files. Defaults under `--output-dir`. |
| `--pending-dir` | No | Base directory for task-sharded pending-volume files. Defaults under `--output-dir`. |
| `--filter1` / `--no-filter1` | No | Enable/disable Filter 1 (Standard Stopwords). Default: enabled. |
| `--filter2` / `--no-filter2` | No | Enable/disable Filter 2 (Min Frequency). Default: enabled. |
| `--filter3` / `--no-filter3` | No | Enable/disable Filter 3 (PMI). Default: enabled. |
| `--min-freq` | No | Minimum frequency supplied to Filter 2. |
| `--min-pmi` | No | Minimum PMI supplied to Filter 3. |
| `--max-exposure` | No | Compatibility parameter for the experimental Filter 4 helper. It is recorded but does not currently alter output. |
| `--max-zip-size-mb` | No | Skip source ZIPs larger than this many MiB. Default: `150`. |
| `--flat-output` | No | Writes volume TSVs into one directory instead of a Pairtree-like hierarchy. |

The SLURM wrapper provides deployment defaults through environment variables.
Set `VENV_PATH`, `STANZA_RESOURCES_DIR`, `NLTK_DATA`, `PAIRTREE_ROOT`,
`BIBFRAME_ROOT`, `ID_LIST`, `RUN_ROOT`, `OUTPUT_DIR`, `AUDIT_DIR`,
`METADATA_DIR`, `SUMMARY_DIR`, `PENDING_DIR`, or `MAX_ZIP_SIZE_MB` before
submission to override a default without editing the script.

## SLURM Execution

`submit.sh` counts nonempty HTIDs and submits a SLURM array through
`submit_bigred.slurm`.

```bash
mkdir -p logs
bash submit.sh
```

Each array task receives a stride-based subset of the HTID list. Within each
task, `SLURM_CPUS_PER_TASK` determines the multiprocessing worker count.

Monitor a submitted job with the scheduler tools provided by the cluster:

```bash
squeue -u "$USER"
```

The pipeline uses checkpoint validation: if a target TSV already exists and possesses both a valid header and a final newline byte, that volume is treated as complete and skipped. Use separate output locations or clear prior outputs when intentionally rerunning an HTID with different filter settings.

## Recovery and Retry

The pipeline is designed to resume after a cancelled job or node failure.
Completed TSVs with a recognized header and final newline are checkpointed and
skipped. An existing incomplete TSV is replaced through the atomic export path.

Each array task writes a pending-volume TSV containing `htid`, `reason_code`,
and `detail`. Review the reason codes before retrying: missing source data and
unsupported languages will normally fail again, while corrected permissions,
dependencies, resources, or transient read problems may be retryable.

To collect all pending HTIDs from one run:

```bash
find runs/current_run/pending/slurm_<job_id> \
  -type f -name 'task_*.tsv' -print0 \
  | xargs -0 awk -F '\t' 'FNR > 1 {print $1}' \
  | sort -u > htids_retry.txt
```

Inspect `reason_code` and `detail`, correct the underlying problem, then submit
`htids_retry.txt` with the same filter settings and output directory:

```bash
ID_LIST=htids_retry.txt bash submit.sh
```

Existing completed volumes will be skipped. A retry with changed filters or
thresholds must use a new output directory because `run_metadata.json`
intentionally rejects configuration conflicts.

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
└── src/
    ├── config.py
    ├── engine_export.py
    ├── engine_filters.py
    ├── engine_io.py
    ├── engine_nlp.py
    ├── metrics.py
    └── provenance.py
```

## Operational Notes

- **Atomic Writes:** Volume TSVs are written to a `.tmp` file first and then atomically renamed to `.tsv` to reduce the risk of data corruption even if a node crashes mid-write.
- **Lockless Chunking:** Results and pending volume logs are not written to shared files. Each SLURM array task writes its own file beneath the configured summary and pending directories (for example, `<summary-dir>/<run_id>/task_0001.csv`). This is a shared-nothing architecture designed specifically for Lustre filesystems such as BigRed200. Merge these files downstream if a single metadata log is required.
- **Configuration Fingerprinting:** If you restart a run using an existing `--output-dir`, the script reads `run_metadata.json` from that output directory to verify that your filter configuration exactly matches the existing files. If you change a parameter (like `--min-freq`), the pipeline will abort to prevent silent data corruption.
- **Structured Failure Reporting:** Task-specific pending TSVs contain `htid`, `reason_code`, and `detail`. Stable codes distinguish missing, corrupt, unreadable, oversized, and permission-restricted inputs from language, dependency, and tokenizer failures.
- **Page Diagnostics:** Task result CSVs record source text pages, readable pages, failed page reads, and Latin-1 decoding fallbacks. Up to five page examples are printed in the task log for each warning category.
- **Output Metrics:** Task result CSVs distinguish total tokens, retained distinct bigram types, retained bigram occurrences, singleton percentage, and the actual completed TSV size. Storage reporting is based on the exported TSV rather than an estimated serialization format.
- Pairtree ZIPs are streamed page by page rather than loaded as one volume.
- Stanza pipelines are lazily initialized and cached once per worker process.
- The configured ZIP-size guard skips compressed input files above the deployment threshold.

## Related Documentation

- [`README_EXPORT.md`](README_EXPORT.md): export schema and metadata reference.
- [`limitations.md`](limitations.md): language-by-language data-quality review,
  tokenizer behavior, evidence status, and known interpretation limitations.
