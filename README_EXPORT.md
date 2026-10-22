# HTRC N-grams Export Specification

## Scope

This document defines the volume-level bigram TSV files and supporting metadata
that may be distributed to researchers.

Each TSV contains aggregated bigram types from one HathiTrust volume. It does
not contain page numbers, token positions, sentence identifiers, or the
original page text. Published provenance provides context for how the TSV was
created.

## Bigram TSV

### Format

| Property | Value |
| --- | --- |
| Encoding | UTF-8 |
| Compression | Gzip, direct atomic export (`.tsv.gz`) |
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

### Field Definitions

| Field | Type | Required | Definition |
| --- | --- | --- | --- |
| `w1` | UTF-8 string | Yes | First token in the adjacent pair. |
| `w2` | UTF-8 string | Yes | Second token in the adjacent pair. |
| `freq` | Positive integer | Yes | Number of occurrences of the exact pair in the tokenized volume. |

`freq` is not a page count, normalized rate, corpus frequency, or document
frequency.

## Token Semantics

Tokens are written as returned by the preprocessing and Stanza tokenization
path.

- Case is preserved: `The` and `the` are distinct.
- Numbers are retained.
- Punctuation can appear in either token column.
- Inline hyphens become independent `-` tokens.
- Words split by a line-final hyphen are recombined before tokenization.
- Stanza MWT processing can expand contractions and clitics.
- Pages are sent to Stanza in batches of up to 100, but Stanza returns each page
  separately and bigrams are generated independently for each page.

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

## Frequency

Bigram frequency is calculated from exact token tuples. Case variants and
different tokenizations are counted separately:

```text
The	dog	12
the	dog	47
```

## Row Ordering

Rows are exported in the counter's insertion order. The production pipeline
intentionally avoids a volume-wide frequency sort because row order is not part
of the dataset contract. Consumers should apply their own explicit sort when a
particular ordering is required.

## Run Provenance JSON

`metadata/<run_id>.json` records shared processing context in the default
BigRed200 directory layout.

Current fields include:

| Field | Description |
| --- | --- |
| `run_id` | Run identifier shared by array tasks. |
| `created_at_utc` | UTC manifest creation timestamp. |
| `git_commit` | Git commit when available. |
| `deployment_method` | Detected deployment context. |
| `python_version` | Python runtime version. |
| `stanza_version` | Installed Stanza package version. |
| `langid_version` | Installed langid package version. |
| `export_policy` | Export policy used for the run; currently `unfiltered`. |
| `output_format` | Export container; currently `tsv.gz`. |
| `gzip_level` | Gzip compression level used by the run. |
| `stanza_models` | Tokenizer and MWT package names and manifest MD5 values. |

The first SLURM array task writes the shared manifest once for the run. Atomic
file creation prevents a partial manifest from being published.

Wave campaigns also publish compact status JSON. Each wave records selected,
attempted, successful, checkpointed, and pending counts. The final
`status/campaigns/<campaign-name>/campaign_summary.json` is written only after
the counts reconcile with the campaign manifest.

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
| `total_tokens` | Number of tokens produced. Punctuation and numbers count as tokens. |
| `distinct_bigram_types` | Number of distinct exported bigram rows. |
| `total_bigram_occurrences` | Sum of `freq` across all exported bigram rows. |
| `singleton_percentage` | Percentage of exported bigram types whose `freq` equals one. |
| `tsv_size_bytes` | Actual size of the completed volume TSV measured from the filesystem. |
| `tsv_size_mb` | `tsv_size_bytes` converted to MiB using 1,048,576 bytes per MiB. |

`distinct_bigram_types` is a per-volume count. When task summaries add these
values across volumes, the result is explicitly labeled as the sum of
volume-level types; it is not a deduplicated corpus-wide vocabulary count.

### Pending-Volume TSV

Array tasks write temporary shards during processing. After the array reaches a
terminal state, a dependent finalization job consolidates the available shards
and writes one final file:

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
| `MISSING_DEPENDENCY` | A required Python package is unavailable. |
| `UNEXPECTED_ERROR` | An uncategorized exception reached the volume boundary. |

## Reading TSV Files

Use a CSV-aware reader configured with a tab delimiter.

### Python Standard Library

```python
import csv
import gzip

with gzip.open(
    "volume.tsv.gz",
    "rt",
    encoding="utf-8",
    newline="",
) as input_file:
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
    "volume.tsv.gz",
    sep="\t",
    encoding="utf-8",
    compression="gzip",
    keep_default_na=False,
)
```

`keep_default_na=False` prevents valid tokens such as `NA` from being converted
to missing values.

### R

```r
data <- read.delim(
  gzfile("volume.tsv.gz", open = "rt"),
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
import gzip

with gzip.open(
    "volume.tsv.gz",
    "rt",
    encoding="utf-8",
    newline="",
) as input_file:
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

The expected header is:

```text
w1	w2	freq
```

## Interpretation Limitations

Exports inherit limitations from OCR, bibliographic metadata, page structure,
and language models. They may contain:

- OCR substitutions and segmentation errors;
- scanner text not recognized by the boilerplate markers;
- multilingual passages inside a volume assigned one language;
- valid legal, scientific, bibliographic, or other domain-specific language;
- punctuation-heavy bigrams; and
- language-specific token units that differ from a researcher's preferred
  analysis.

Historical spelling and specialized vocabulary are not automatically errors.
Researchers should review frequency, provenance, and the documented language
limitations together.
