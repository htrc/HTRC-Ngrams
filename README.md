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
- four filtering operations;
- per-volume stopword audit files;
- run-level provenance metadata; and
- checkpointed TSV export.

This repository is under active research and data-quality review. Filtering can
reduce disclosure risk and dataset size, but the software does not by itself
guarantee that an export satisfies a particular legal, policy, or
non-consumptive-data standard. Collections should be evaluated under the
requirements of the institution releasing them.

## Processing Overview

For each HTID, the pipeline:

1. Resolves the Pairtree ZIP path.
2. Resolves one language for the volume from JSON-LD metadata when available.
3. Uses `langid` as a constrained fallback across the 20 supported languages.
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

Tokenizer behavior is model-specific. Known behavior discovered during
validation is documented in [`limitations.md`](limitations.md).

## Filtering

The filtering layer defines four operations. Filter configuration is recorded
with run provenance so that exported collections can be interpreted
consistently.

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

### Filter 4: Exposure Cap

Limits retained bigram occurrences relative to the original volume token count.
The implementation computes:

```text
maximum bigram occurrences = floor(max_exposure * total_tokens / 2)
```

It retains rows in their ranked order until adding another row would exceed the
allowed occurrence budget. The command-line default for `max_exposure` is
`0.15`.

## Output Artifacts

A run can create the following artifacts:

| Path | Purpose |
| --- | --- |
| `exported_bigrams/` or `exported_bigrams_flat/` | Volume-level bigram TSV files. |
| `results/<run_id>.csv` | Per-volume processing metrics and metadata. |
| `provenance/<run_id>.json` | Run-level software, filter, and Stanza model provenance. |
| `stopwords/top25_stopwords/` | Selected per-volume stopwords and frequencies. |
| `stopwords/top25_dropped_bigrams/` | Bigrams removed by Filter 1. |
| `pending_volumes.txt` | Volumes not exported and their reason codes. |
| `logs/` | SLURM task output and error logs. |

See [`README_EXPORT.md`](README_EXPORT.md) for the complete TSV and metadata
specification.

## Requirements

- Python 3.9 or newer
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

Stanza models should be downloaded before submitting large jobs. Compute nodes
should not be expected to download models during processing.

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

The deployment may provide HTRC JSON-LD bibliographic metadata. When a language
value is available, it is preferred over statistical fallback detection.
Language assignment is volume-level rather than page-level.

## Running the Pipeline

The main entry point is `run_pipeline.py`.

```bash
python3 run_pipeline.py \
  --pairtree /path/to/ht_text \
  --id-list /path/to/htids.txt \
  --min-freq 2 \
  --min-pmi 0.0 \
  --max-exposure 0.15 \
  --flat-output
```

### Arguments

| Argument | Required | Description |
| --- | --- | --- |
| `--pairtree` | Yes | Pairtree root containing source ZIP volumes. |
| `--id-list` | Yes | UTF-8 text file containing one HTID per line. |
| `--min-freq` | No | Minimum frequency supplied to Filter 2. |
| `--min-pmi` | No | Minimum PMI supplied to Filter 3. |
| `--max-exposure` | No | Exposure ratio parameter defined for Filter 4. |
| `--flat-output` | No | Writes volume TSVs into one directory instead of a Pairtree-like hierarchy. |

The current HPC deployment paths are configured in `run_pipeline.py` and
`submit_bigred.slurm`. Adapt those paths for the target environment before
running outside the original deployment.

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

The pipeline uses checkpointing: if a target TSV already exists, that volume is
skipped. Use separate output locations or clear prior outputs when intentionally
rerunning an HTID with different filter settings.

## Validation

Validate all configured Stanza tokenizers:

```bash
python3 scripts/validate_stanza_languages.py
```

The validator checks:

- all 20 language mappings and profiles;
- tokenizer and MWT processor availability;
- punctuation, numbers, and capitalization;
- English contraction and hyphen behavior;
- French elision behavior; and
- Stanza model package names and checksums.

For an HPC smoke test:

```bash
mkdir -p logs
sbatch smoke_test_bigred.slurm
```

## Repository Layout

```text
.
├── run_pipeline.py
├── requirements.txt
├── submit.sh
├── submit_bigred.slurm
├── smoke_test_bigred.slurm
├── scripts/
│   └── validate_stanza_languages.py
└── src/
    ├── config.py
    ├── engine_filters.py
    ├── engine_io.py
    ├── engine_nlp.py
    ├── metrics.py
    └── provenance.py
```

## Operational Notes

- Pairtree ZIPs are streamed page by page rather than loaded as one volume.
- Stanza pipelines are lazily initialized and cached once per worker process.
- Result and pending-volume files use lock files for concurrent writes.
- Large array runs should monitor lock contention, stale lock files, worker
  memory, and filesystem metadata load.
- The configured ZIP-size guard skips compressed input files above the
  deployment threshold.
- Output paths should include enough run context to avoid confusing artifacts
  from different executions.

## Related Documentation

- [`README_EXPORT.md`](README_EXPORT.md): export schema and metadata reference.
- [`limitations.md`](limitations.md): known tokenizer and interpretation
  limitations.
