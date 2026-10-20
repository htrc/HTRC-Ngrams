# HTRC N-grams Export Specification

## Scope

This document defines the volume-level bigram TSV files and supporting metadata
that may be distributed to researchers.

Each TSV contains aggregated bigram types from one HathiTrust volume. It does
not contain page numbers, token positions, sentence identifiers, or the
original page text. Published provenance provides context for how the TSV was
created. Stopword audit information exists only for runs that enable Filter 1.

## Bigram TSV

### Format

| Property | Value |
| --- | --- |
| Encoding | UTF-8 |
| Field delimiter | Tab |
| Record delimiter | Newline |
| Header | Present |
| Quoting | CSV `QUOTE_MINIMAL` behavior |
| Quote character | `"` |
| Aggregation unit | One volume |
| Row unit | One distinct, case-sensitive bigram type |

The files are written with Python's `csv.writer` using a tab delimiter.
CSV-aware readers should therefore retain normal quote processing. A token that
contains a quotation mark is represented with CSV escaping.

For example, a bigram containing two literal double-quote tokens with frequency
`473` is stored in the raw TSV as:

```text
""""<TAB>""""<TAB>473
```

Each `""""` field decodes to one `"` token. It does not represent four quote
tokens or a damaged row. A CSV-aware tab-delimited reader returns the logical
values `['"', '"', '473']`. Do not parse these files with a plain string split
that ignores CSV quoting.

### Three-Column Schema

```text
w1<TAB>w2<TAB>freq
```

Example:

```text
w1	w2	freq
peanut	butter	200
baseball	.	42
.	It	31
```

### Four-Column Schema

When PMI is included:

```text
w1<TAB>w2<TAB>freq<TAB>pmi
```

Example:

```text
w1	w2	freq	pmi
peanut	butter	200	6.417
baseball	.	42	0.731
```

### Field Definitions

| Field | Type | Required | Definition |
| --- | --- | --- | --- |
| `w1` | UTF-8 string | Yes | First token in the adjacent pair. |
| `w2` | UTF-8 string | Yes | Second token in the adjacent pair. |
| `freq` | Positive integer | Yes | Number of occurrences of the exact pair in the tokenized volume. |
| `pmi` | Decimal | When PMI is included | Volume-level PMI rounded to three decimal places. |

`freq` is not a page count, normalized rate, corpus frequency, or document
frequency. Filters remove complete rows; they do not alter the retained row's
original volume-level occurrence count.

## Token Semantics

Tokens are written as returned by the preprocessing and Stanza tokenization
path.

- Case is preserved: `The` and `the` are distinct.
- Numbers are retained.
- Punctuation can appear in either token column.
- Inline hyphens become independent `-` tokens.
- Words split by a line-final hyphen are recombined before tokenization.
- Stanza MWT processing can expand contractions and clitics.
- Bigrams are generated independently for each page.

Examples:

| Source | Tokens | Bigrams |
| --- | --- | --- |
| `can't go` | `ca`, `n't`, `go` | `ca n't`; `n't go` |
| `Mark's book` | `Mark`, `'s`, `book` | `Mark 's`; `'s book` |
| `all-too-lazy` | `all`, `-`, `too`, `-`, `lazy` | `all -`; `- too`; `too -`; `- lazy` |
| `baseball. It` | `baseball`, `.`, `It` | `baseball .`; `. It` |

Because punctuation is retained, `baseball. It` does not create the direct
bigram `baseball It`.

Language-specific behavior may differ. For example, Korean can retain a number
and classifier as one token such as `2권`. See
[`limitations.md`](limitations.md).

## Bigram Generation

For a page token sequence:

```text
t1, t2, t3, t4
```

the generated bigrams are:

```text
(t1, t2), (t2, t3), (t3, t4)
```

The same token can participate in two neighboring bigrams. This does not double
its unigram frequency; bigram frequencies count adjacent pairs rather than
individual words.

## Frequency and PMI

### Frequency

Bigram frequency is calculated from exact token tuples. Case variants and
different tokenizations are counted separately:

```text
The	dog	12
the	dog	47
```

### PMI

PMI is calculated as:

```text
PMI(w1, w2) = log2(P(w1, w2) / (P(w1) * P(w2)))
```

The implementation uses:

```text
P(w1, w2) = bigram frequency / original total bigram occurrences
P(w1)     = unigram frequency of w1 / original total tokens
P(w2)     = unigram frequency of w2 / original total tokens
```

The denominators and unigram counts come from the unfiltered tokenized volume.
Applying earlier filters therefore does not redefine the base probabilities.

PMI can favor rare pairs. It is an association statistic rather than a direct
measurement of linguistic correctness, OCR quality, or research relevance.
Interpret it together with `freq`.

## Filters

The confirmed export policy is unfiltered. Filters 1, 2, 3, and 4 are disabled,
so the production TSV uses the three-column `w1`, `w2`, `freq` schema. Run
provenance and `run_metadata.json` record these states for each execution.

The filtering module also contains an experimental Filter 4 helper, but
`run_pipeline.py` does not call it and it does not alter exported rows.

### Filter 1: Standard Stopword Bigrams

The pipeline selects up to 25 case-folded tokens that:

1. occur in the volume; and
2. belong to the public stopword list configured for the volume language.

A bigram is removed only when both tokens belong to the selected set.

### Filter 2: Minimum Frequency

Retains bigram types satisfying:

```text
freq >= min_freq
```

### Filter 3: PMI Threshold

Retains bigram types satisfying:

```text
pmi >= min_pmi
```

### Filter 4: Exposure Cap

Computes an occurrence budget:

```text
floor(max_exposure * total_tokens / 2)
```

Rows are evaluated in their existing ranked order. Rows are retained until the
next row would exceed the budget. The reported exposure is:

```text
2 * retained bigram occurrences / total_tokens
```

This describes the unused helper only. Filter 4 is disabled and provides no
reconstruction guarantee.

## Row Ordering

When PMI is included, rows are sorted by descending frequency and then
descending PMI.

Without PMI, rows follow `Counter.most_common()`, which orders primarily by
descending frequency. Consumers requiring deterministic tie ordering should
apply an explicit secondary sort.

## Run Provenance JSON

`provenance/<run_id>.json` records shared processing context.

Current fields include:

| Field | Description |
| --- | --- |
| `run_id` | Run identifier shared by array tasks. |
| `created_at_utc` | UTC manifest creation timestamp. |
| `git_commit` | Git commit when available. |
| `deployment_method` | Detected deployment context. |
| `python_version` | Python runtime version. |
| `stanza_version` | Installed Stanza package version. |
| `nltk_version` | Installed NLTK package version. |
| `stopwordsiso_version` | Installed stopwordsiso package version. |
| `langid_version` | Installed langid package version. |
| `filters` | Recorded filter switches and numeric thresholds. |
| `stanza_models` | Tokenizer and MWT package names and manifest MD5 values. |

The manifest is created with exclusive file creation so concurrent array tasks
do not write duplicate manifests.

## Operational Diagnostics

When `--summary-dir` is supplied, operational diagnostics are task-sharded to
avoid multiple SLURM workers writing to the same file. The production SLURM
wrapper omits this option, so these CSV files are not generated by default.
They support pipeline review and do not need to be distributed with the
research dataset.

### Task Result CSV

When enabled, each successful-volume row includes page-reading diagnostics:

| Field | Definition |
| --- | --- |
| `source_text_pages` | Number of `.txt` page members found in the source ZIP. |
| `total_pages` | Number of page members successfully decoded and processed. |
| `failed_pages` | Number of page members that could not be read. |
| `decoding_warnings` | Number of pages decoded with the Latin-1 fallback after UTF-8 decoding failed. |

It also records the following volume-level output metrics:

| Field | Definition |
| --- | --- |
| `total_tokens` | Number of tokens produced before bigram filters are applied. Punctuation and numbers count as tokens. |
| `distinct_bigram_types` | Number of distinct bigram rows retained after the configured filters. |
| `total_bigram_occurrences` | Sum of `freq` across all retained bigram rows. |
| `singleton_percentage` | Percentage of retained bigram types whose `freq` equals one. |
| `tsv_size_bytes` | Actual size of the completed volume TSV measured from the filesystem. |
| `tsv_size_mb` | `tsv_size_bytes` converted to MiB using 1,048,576 bytes per MiB. |

`distinct_bigram_types` is a per-volume count. When task summaries add these
values across volumes, the result is explicitly labeled as the sum of
volume-level types; it is not a deduplicated corpus-wide vocabulary count.

### Pending-Volume TSV

Array tasks write temporary shards during processing. After every SLURM array
task finishes successfully, a dependent consolidation job writes one final
file:

```text
pending/pending_volumes.tsv
```

The final file uses this schema:

```text
htid<TAB>reason_code<TAB>detail
```

`reason_code` is stable for programmatic grouping. `detail` contains bounded
human-readable context and is not intended as a stable identifier.

The consolidation step deduplicates by HTID, atomically replaces the previous
final file, and removes the completed run's temporary pending shards. After a
retry, the final file therefore contains only volumes that remain pending in
that retry. Concurrent runs should use different pending directories.

| Reason code | Meaning |
| --- | --- |
| `ZIP_NOT_FOUND` | The computed source ZIP path does not exist. |
| `ZIP_CORRUPT` | Python could not open the input as a valid ZIP archive. |
| `ZIP_PERMISSION_DENIED` | The process could not access the source ZIP. |
| `ZIP_READ_ERROR` | Another operating-system ZIP read error occurred. |
| `ZIP_TOO_LARGE` | The compressed ZIP exceeds the configured size limit. |
| `NO_TEXT_PAGES` | The ZIP contains no `.txt` page members. |
| `NO_READABLE_PAGES` | Text page members exist, but all failed during reading. |
| `NO_TOKENS_FOUND` | Pages were read, but tokenization produced no tokens. |
| `METADATA_PERMISSION_DENIED` | Bibframe metadata could not be read because of permissions. |
| `METADATA_ACCESS_ERROR` | Another operating-system metadata access error occurred. |
| `LOW_LANGUAGE_CONFIDENCE` | Text-based language resolution was not reliable. |
| `UNSUPPORTED_LANGUAGE` | Metadata or detected language is outside configured support. |
| `UNSUPPORTED_STANZA_LANGUAGE` | No Stanza mapping exists for the resolved language. |
| `STANZA_PIPELINE_UNAVAILABLE` | The configured Stanza pipeline could not initialize. |
| `UNSUPPORTED_STOPWORD_LANGUAGE` | No configured stopword source exists for the language. |
| `MISSING_DEPENDENCY` | A required Python package is unavailable. |
| `UNEXPECTED_ERROR` | An uncategorized exception reached the volume boundary. |

## Optional Stopword Audit Information

The unfiltered export does not generate stopword audit files. If Filter 1 is
enabled for an experimental run, the following file is created for each
processed volume.

### Selected Stopwords

```text
stopwords/top25_stopwords/<htid>_top25.txt
```

Schema:

```text
word<TAB>freq
```

`word` is case-folded. `freq` combines case variants from the tokenized volume.

## Reading TSV Files

Use a CSV-aware reader configured with a tab delimiter.

### Python Standard Library

```python
import csv

with open("volume.tsv", "r", encoding="utf-8", newline="") as input_file:
    reader = csv.DictReader(input_file, delimiter="\t")
    for row in reader:
        word1 = row["w1"]
        word2 = row["w2"]
        frequency = int(row["freq"])
```

### Pandas

```python
import pandas as pd

data = pd.read_csv(
    "volume.tsv",
    sep="\t",
    encoding="utf-8",
    keep_default_na=False,
)
```

`keep_default_na=False` prevents valid tokens such as `NA` from being converted
to missing values.

### R

```r
data <- read.delim(
  "volume.tsv",
  sep = "\t",
  quote = "\"",
  fileEncoding = "UTF-8",
  stringsAsFactors = FALSE,
  check.names = FALSE
)
```

## Validation

Validate a file with Python's TSV parser:

```python
import csv

with open("volume.tsv", encoding="utf-8", newline="") as input_file:
    rows = csv.reader(input_file, delimiter="\t")
    header = next(rows)
    expected_fields = len(header)

    for line_number, row in enumerate(rows, start=2):
        if len(row) != expected_fields:
            raise ValueError(
                f"Line {line_number} has {len(row)} fields; "
                f"expected {expected_fields}"
            )
```

The expected header is either:

```text
w1	w2	freq
```

or:

```text
w1	w2	freq	pmi
```

## Interpretation Limitations

Exports inherit limitations from OCR, bibliographic metadata, page structure,
language models, and filtering choices. They may contain:

- OCR substitutions and segmentation errors;
- scanner text not recognized by the boilerplate markers;
- multilingual passages inside a volume assigned one language;
- valid legal, scientific, bibliographic, or other domain-specific language;
- punctuation-heavy bigrams; and
- language-specific token units that differ from a researcher's preferred
  analysis.

Historical spelling and specialized vocabulary are not automatically errors.
Researchers should review frequency, optional PMI, provenance, optional
stopword audits, and the documented language limitations together.
