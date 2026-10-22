#!/usr/bin/env python3
"""Create task-sized, language-homogeneous HTID shards from a Hathifile."""

import argparse
import csv
import math
import random
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine_nlp import canonicalize_language, is_supported_language
from src.engine_io import htid_to_zip_path


def load_records(input_path: Path, limit: int, sample_seed: int = None):
    if sample_seed is not None and not limit:
        raise RuntimeError("--sample-seed requires a positive --limit")

    if sample_seed is not None:
        reservoir = []
        random_generator = random.Random(sample_seed)
        eligible_count = 0

        with open(input_path, "r", encoding="utf-8", newline="") as file:
            reader = csv.reader(file, delimiter="\t")
            for row in reader:
                if not row:
                    continue
                htid = row[0].strip()
                language = row[1].strip().lower() if len(row) > 1 else ""
                if not htid or htid.lower() == "htid":
                    continue

                eligible_count += 1
                record = (htid, canonicalize_language(language))
                if len(reservoir) < limit:
                    reservoir.append(record)
                    continue

                replacement = random_generator.randrange(eligible_count)
                if replacement < limit:
                    reservoir[replacement] = record

        if eligible_count < limit:
            raise RuntimeError(
                f"Requested {limit:,} records but found only {eligible_count:,}."
            )
        if len({htid for htid, _ in reservoir}) != len(reservoir):
            raise RuntimeError(
                "The sampled Hathifile records contain duplicate HTIDs."
            )
        return reservoir

    records = []
    seen = set()

    with open(input_path, "r", encoding="utf-8", newline="") as file:
        reader = csv.reader(file, delimiter="\t")
        for row in reader:
            if not row:
                continue

            htid = row[0].strip()
            language = row[1].strip().lower() if len(row) > 1 else ""
            if not htid or htid.lower() == "htid" or htid in seen:
                continue

            seen.add(htid)
            records.append((htid, canonicalize_language(language)))
            if limit and sample_seed is None and len(records) == limit:
                break

    return records


def create_shards(
    input_path: Path,
    output_dir: Path,
    limit: int,
    target_volumes: int,
    sample_seed: int,
    pairtree_root: Path,
    overwrite: bool,
) -> Path:
    if output_dir.exists():
        if not overwrite:
            raise RuntimeError(
                f"Shard output already exists: {output_dir}. "
                "Use --overwrite only when it is safe to replace it."
            )
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True)
    records = load_records(input_path, limit, sample_seed)
    if not records:
        raise RuntimeError(f"No HTID/language records found in {input_path}")
    if limit and len(records) < limit:
        raise RuntimeError(
            f"Requested {limit:,} unique records but found only {len(records):,}."
        )

    records_by_language = defaultdict(list)
    language_counts = defaultdict(int)
    missing_zip_count = 0
    for htid, language in records:
        normalized_language = language or "unknown"
        language_counts[normalized_language] += 1
        group = (
            normalized_language
            if is_supported_language(normalized_language)
            else "unsupported"
        )
        zip_size_bytes = None
        if pairtree_root is not None:
            try:
                zip_size_bytes = htid_to_zip_path(
                    htid, pairtree_root
                ).stat().st_size
            except FileNotFoundError:
                missing_zip_count += 1
        records_by_language[group].append(
            (htid, normalized_language, zip_size_bytes)
        )

    manifest_path = output_dir / "shards_manifest.txt"
    manifest_rows = []

    for language in sorted(records_by_language):
        language_records = records_by_language[language]
        shard_count = math.ceil(len(language_records) / target_volumes)

        if pairtree_root is not None:
            shard_records = [[] for _ in range(shard_count)]
            shard_sizes = [0 for _ in range(shard_count)]
            sorted_records = sorted(
                language_records,
                key=lambda record: (-(record[2] or 0), record[0]),
            )
            for record in sorted_records:
                available = [
                    index
                    for index, shard in enumerate(shard_records)
                    if len(shard) < target_volumes
                ]
                destination = min(
                    available,
                    key=lambda index: (shard_sizes[index], len(shard_records[index])),
                )
                shard_records[destination].append(record)
                shard_sizes[destination] += record[2] or 0
        else:
            shard_records = [
                language_records[index:index + target_volumes]
                for index in range(0, len(language_records), target_volumes)
            ]

        for shard_index, records_for_shard in enumerate(shard_records):
            shard_path = output_dir / f"{language}_{shard_index:04d}.tsv"

            with open(shard_path, "w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter="\t", lineterminator="\n")
                writer.writerow(["htid", "language", "zip_size_bytes"])
                writer.writerows(records_for_shard)

            manifest_rows.append(shard_path.resolve())

    with open(manifest_path, "w", encoding="utf-8", newline="") as file:
        for shard_path in manifest_rows:
            file.write(f"{shard_path}\n")

    supported = sum(
        count
        for language, count in language_counts.items()
        if is_supported_language(language)
    )
    unsupported = len(records) - supported

    print(f"Selected records: {len(records):,}")
    print(f"Languages: {len(language_counts):,}")
    print(f"Supported records: {supported:,}")
    print(f"Unsupported records retained for pending reporting: {unsupported:,}")
    print(f"Task shards: {len(manifest_rows):,}")
    if pairtree_root is not None:
        print(f"Records missing from Pairtree during sharding: {missing_zip_count:,}")
    print(f"Shard manifest: {manifest_path.resolve()}")

    for language in sorted(language_counts):
        status = "supported" if is_supported_language(language) else "unsupported"
        print(
            f"  {language}: {language_counts[language]:,} "
            f"record(s), {status}"
        )

    return manifest_path


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Create language-specific task shards from a tab-separated "
            "Hathifile whose first two columns are HTID and language."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--target-volumes", type=int, default=100)
    parser.add_argument("--sample-seed", type=int)
    parser.add_argument("--pairtree-root", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.limit < 0:
        parser.error("--limit cannot be negative")
    if args.target_volumes <= 0:
        parser.error("--target-volumes must be greater than zero")

    create_shards(
        args.input,
        args.output_dir,
        args.limit,
        args.target_volumes,
        args.sample_seed,
        args.pairtree_root,
        args.overwrite,
    )


if __name__ == "__main__":
    main()
