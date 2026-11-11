#!/usr/bin/env python3
"""Report finalized campaign work against the original production waves."""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from create_remaining_shard_manifest import normalize_path, wave_is_finalized


DEFAULT_CAMPAIGNS = (
    "initial",
    "rolling",
    "third_campaign",
    "fourth_campaign",
)
SCHEMA_VERSION = "1.0"


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def load_manifest_paths(path: Path, expected_count: int) -> list[str]:
    with path.open(encoding="utf-8") as file:
        paths = [
            str(normalize_path(line.strip()))
            for line in file
            if line.strip()
        ]
    if len(paths) != expected_count:
        raise RuntimeError(
            f"{path} contains {len(paths):,} shard paths; "
            f"expected {expected_count:,}"
        )
    if len(paths) != len(set(paths)):
        raise RuntimeError(f"Duplicate shard path in {path}")
    return paths


def compact_ranges(values: list[int]) -> str:
    if not values:
        return "None"
    ranges = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=60)
    connection.execute("PRAGMA busy_timeout = 60000")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS original_shards (
            path TEXT PRIMARY KEY,
            original_wave INTEGER NOT NULL,
            accounted INTEGER NOT NULL DEFAULT 0
                CHECK (accounted IN (0, 1))
        );
        CREATE INDEX IF NOT EXISTS original_shards_wave_idx
            ON original_shards(original_wave);
        CREATE TABLE IF NOT EXISTS indexed_original_waves (
            wave_index INTEGER PRIMARY KEY,
            shard_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS processed_campaign_waves (
            campaign TEXT NOT NULL,
            wave_index INTEGER NOT NULL,
            shard_count INTEGER NOT NULL,
            processed_at_utc TEXT NOT NULL,
            PRIMARY KEY (campaign, wave_index)
        );
        """
    )
    return connection


def metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return dict(connection.execute("SELECT key, value FROM metadata"))


def initialize_original_index(
    connection: sqlite3.Connection,
    manifest_path: Path,
) -> tuple[dict, int]:
    manifest_path = normalize_path(str(manifest_path))
    campaign = load_json(manifest_path)
    waves = campaign.get("waves")
    wave_count = int(campaign["wave_count"])
    total_shards = int(campaign["total_shards"])
    if not isinstance(waves, list) or len(waves) != wave_count:
        raise RuntimeError("Original campaign wave count is invalid")
    if [int(wave["wave_index"]) for wave in waves] != list(range(wave_count)):
        raise RuntimeError("Original campaign waves are incomplete or out of order")

    saved = metadata(connection)
    expected_metadata = {
        "schema_version": SCHEMA_VERSION,
        "original_campaign_manifest": str(manifest_path),
        "original_wave_count": str(wave_count),
        "original_total_shards": str(total_shards),
    }
    if saved:
        for key, value in expected_metadata.items():
            if saved.get(key) != value:
                raise RuntimeError(
                    f"Monitor database metadata mismatch for {key}; "
                    "use --rebuild to recreate it"
                )
    else:
        with connection:
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                expected_metadata.items(),
            )

    indexed = {
        int(row[0])
        for row in connection.execute(
            "SELECT wave_index FROM indexed_original_waves"
        )
    }
    newly_indexed = 0
    for wave in waves:
        wave_index = int(wave["wave_index"])
        if wave_index in indexed:
            continue
        expected_count = int(wave["task_count"])
        wave_manifest = normalize_path(wave["manifest"])
        paths = load_manifest_paths(wave_manifest, expected_count)
        try:
            with connection:
                connection.executemany(
                    "INSERT INTO original_shards(path, original_wave) VALUES (?, ?)",
                    ((path, wave_index) for path in paths),
                )
                connection.execute(
                    "INSERT INTO indexed_original_waves(wave_index, shard_count) "
                    "VALUES (?, ?)",
                    (wave_index, len(paths)),
                )
        except sqlite3.IntegrityError as error:
            raise RuntimeError(
                f"Duplicate original shard while indexing wave {wave_index}"
            ) from error
        newly_indexed += 1
        if newly_indexed % 10 == 0 or wave_index == wave_count - 1:
            print(
                f"Original-wave index progress: "
                f"{len(indexed) + newly_indexed}/{wave_count} waves",
                flush=True,
            )

    indexed_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM indexed_original_waves"
        ).fetchone()[0]
    )
    shard_count = int(
        connection.execute("SELECT COUNT(*) FROM original_shards").fetchone()[0]
    )
    if indexed_count != wave_count or shard_count != total_shards:
        raise RuntimeError(
            f"Original index is incomplete: {indexed_count}/{wave_count} waves, "
            f"{shard_count:,}/{total_shards:,} shards"
        )
    return campaign, newly_indexed


def ingest_campaign(
    connection: sqlite3.Connection,
    run_root: Path,
    campaign_name: str,
) -> tuple[int, int, int]:
    campaign_manifest = (
        run_root
        / "control"
        / campaign_name
        / "waves"
        / "campaign_manifest.json"
    )
    if not campaign_manifest.is_file():
        return 0, 0, 0

    campaign = load_json(campaign_manifest)
    waves = campaign.get("waves")
    wave_count = int(campaign["wave_count"])
    if not isinstance(waves, list) or len(waves) != wave_count:
        raise RuntimeError(f"Invalid wave count for campaign {campaign_name}")
    if [int(wave["wave_index"]) for wave in waves] != list(range(wave_count)):
        raise RuntimeError(
            f"Campaign {campaign_name} waves are incomplete or out of order"
        )

    pending_dir = run_root / "pending" / "campaigns" / campaign_name / "waves"
    status_dir = run_root / "status" / "campaigns" / campaign_name / "waves"
    processed = {
        int(row[0])
        for row in connection.execute(
            "SELECT wave_index FROM processed_campaign_waves WHERE campaign = ?",
            (campaign_name,),
        )
    }
    connection.execute(
        "CREATE TEMP TABLE IF NOT EXISTS current_wave_shards "
        "(path TEXT PRIMARY KEY)"
    )

    newly_processed = 0
    for wave in waves:
        wave_index = int(wave["wave_index"])
        if wave_index in processed:
            continue
        if not wave_is_finalized(wave_index, pending_dir, status_dir):
            continue

        expected_count = int(wave["task_count"])
        status_path = status_dir / f"wave_{wave_index:04d}.json"
        status = load_json(status_path)
        if int(status["task_count"]) != expected_count:
            raise RuntimeError(
                f"Campaign {campaign_name} wave {wave_index} status records "
                f"{status['task_count']} tasks; expected {expected_count}"
            )
        wave_manifest = normalize_path(wave["manifest"])
        paths = load_manifest_paths(wave_manifest, expected_count)

        with connection:
            connection.execute("DELETE FROM current_wave_shards")
            connection.executemany(
                "INSERT INTO current_wave_shards(path) VALUES (?)",
                ((path,) for path in paths),
            )
            unknown = connection.execute(
                """
                SELECT current_wave_shards.path
                FROM current_wave_shards
                LEFT JOIN original_shards
                    ON original_shards.path = current_wave_shards.path
                WHERE original_shards.path IS NULL
                LIMIT 1
                """
            ).fetchone()
            if unknown is not None:
                raise RuntimeError(
                    f"Campaign {campaign_name} wave {wave_index} contains a shard "
                    f"outside the original campaign: {unknown[0]}"
                )
            connection.execute(
                """
                UPDATE original_shards
                SET accounted = 1
                WHERE path IN (SELECT path FROM current_wave_shards)
                """
            )
            connection.execute(
                """
                INSERT INTO processed_campaign_waves(
                    campaign, wave_index, shard_count, processed_at_utc
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    campaign_name,
                    wave_index,
                    len(paths),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        newly_processed += 1

    cached_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM processed_campaign_waves WHERE campaign = ?",
            (campaign_name,),
        ).fetchone()[0]
    )
    return wave_count, cached_count, newly_processed


def report_progress(
    connection: sqlite3.Connection,
    original_campaign: dict,
    database_path: Path,
) -> None:
    rows = [
        (int(wave), int(total), int(accounted or 0))
        for wave, total, accounted in connection.execute(
            """
            SELECT original_wave, COUNT(*), SUM(accounted)
            FROM original_shards
            GROUP BY original_wave
            ORDER BY original_wave
            """
        )
    ]
    wave_count = int(original_campaign["wave_count"])
    if len(rows) != wave_count:
        raise RuntimeError(
            f"Expected {wave_count} original-wave summaries; found {len(rows)}"
        )

    fully_accounted = [wave for wave, total, done in rows if done == total]
    first_incomplete = next(
        (wave for wave, total, done in rows if done != total),
        None,
    )
    contiguous_count = first_incomplete if first_incomplete is not None else wave_count
    accounted_shards = sum(done for _, _, done in rows)
    total_shards = sum(total for _, total, _ in rows)

    print()
    print(f"Original waves: {wave_count} (0-{wave_count - 1})")
    print(
        f"Fully accounted original waves: "
        f"{len(fully_accounted)}/{wave_count}"
    )
    if contiguous_count:
        print(
            f"Completed continuously from the start: "
            f"{contiguous_count} wave(s), 0-{contiguous_count - 1}"
        )
    else:
        print("Completed continuously from the start: 0 waves")

    if first_incomplete is None:
        print("Current original wave: all original waves are accounted")
    else:
        _, total, done = rows[first_incomplete]
        percentage = (done / total * 100.0) if total else 100.0
        print(
            f"Current original wave: {first_incomplete} | "
            f"{done:,}/{total:,} task shards accounted "
            f"({percentage:.1f}%) | {total - done:,} remaining"
        )
        later_complete = [
            wave for wave in fully_accounted if wave > first_incomplete
        ]
        print(
            "Later waves already complete out of order: "
            f"{compact_ranges(later_complete)}"
        )

    print(
        f"Total accounted task shards: "
        f"{accounted_shards:,}/{total_shards:,}"
    )
    print(f"Monitor cache: {database_path}")
    print(
        "Accounted means a finalized campaign report proves every volume in "
        "the task shard ended as either a successful output or a pending record."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--original-campaign", default="initial")
    parser.add_argument("--campaign", action="append", dest="campaigns")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    run_root = normalize_path(str(args.run_root))
    campaigns = list(dict.fromkeys(args.campaigns or DEFAULT_CAMPAIGNS))
    database_path = args.database or (
        run_root / "control" / "original_wave_monitor" / "progress.sqlite3"
    )
    database_path = normalize_path(str(database_path))

    if args.rebuild:
        for suffix in ("", "-journal", "-wal", "-shm"):
            database_path.with_name(database_path.name + suffix).unlink(
                missing_ok=True
            )

    original_manifest = (
        run_root
        / "control"
        / args.original_campaign
        / "waves"
        / "campaign_manifest.json"
    )
    if not original_manifest.is_file():
        raise SystemExit(f"Original campaign manifest not found: {original_manifest}")

    connection = open_database(database_path)
    try:
        original_campaign, newly_indexed = initialize_original_index(
            connection,
            original_manifest,
        )
        if newly_indexed:
            print(f"New original waves indexed: {newly_indexed}")
        for campaign_name in campaigns:
            total, cached, new = ingest_campaign(
                connection,
                run_root,
                campaign_name,
            )
            if total:
                print(
                    f"{campaign_name}: {cached}/{total} finalized campaign "
                    f"waves cached ({new} new)"
                )
            else:
                print(f"{campaign_name}: campaign manifest not present")
        report_progress(connection, original_campaign, database_path)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
