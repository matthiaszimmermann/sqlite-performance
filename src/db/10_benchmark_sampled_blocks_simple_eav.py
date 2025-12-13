#!/usr/bin/env python3
"""Benchmark using sampled blocks from real arkiv data (simple EAV schema, logs string/numeric attribute counts separately).

Derived from 09_benchmark_sampled_blocks.py.

This script samples real blocks from a source database and replays
them into an output database, measuring per-block commit performance.

Schema: Simple EAV (single block column, 2 indexes per attribute table)

Usage:
    uv run python -m db.10_benchmark_sampled_blocks_simple_eav <sample_db> <base_db> <output_db> <num_blocks>
    
Arguments:
    sample_db  - Source database to sample blocks from (e.g., arkiv-data-mendoza.db)
    base_db    - Starting database ("" for fresh start, or existing DB path)
    output_db  - Output database (will be created or appended to)
    num_blocks - Number of blocks to add

Output:
    - Creates/appends to <output_db>
    - Creates CSV file <output_db>.csv with per-block metrics, including string/numeric attribute counts
"""

import shutil
import sqlite3
import sys
import time
import random
import uuid
from pathlib import Path

MIN_ENTITIES_PER_BLOCK = 1
MAX_ENTITIES_PER_BLOCK = 500

def get_valid_blocks(source_conn: sqlite3.Connection) -> list[tuple[int, int]]:
    cursor = source_conn.execute("""
        SELECT from_block, COUNT(DISTINCT entity_key) as entity_count
        FROM payloads
        GROUP BY from_block
        HAVING entity_count >= ? AND entity_count <= ?
        ORDER BY from_block
    """, (MIN_ENTITIES_PER_BLOCK, MAX_ENTITIES_PER_BLOCK))
    return cursor.fetchall()

def create_simple_eav_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS string_attributes (
            entity_key BLOB NOT NULL,
            block INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (entity_key, key, block)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS sa_block_key_idx ON string_attributes (block, key)")
    conn.execute("CREATE INDEX IF NOT EXISTS sa_key_value_idx ON string_attributes (key, value)")
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS numeric_attributes (
            entity_key BLOB NOT NULL,
            block INTEGER NOT NULL,
            key TEXT NOT NULL,
            value INTEGER NOT NULL,
            PRIMARY KEY (entity_key, key, block)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS na_block_key_idx ON numeric_attributes (block, key)")
    conn.execute("CREATE INDEX IF NOT EXISTS na_key_value_idx ON numeric_attributes (key, value)")
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payloads (
            entity_key BLOB NOT NULL,
            block INTEGER NOT NULL,
            payload BLOB NOT NULL,
            content_type TEXT NOT NULL DEFAULT '',
            string_attributes TEXT NOT NULL DEFAULT '{}',
            numeric_attributes TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (entity_key, block)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS p_block_idx ON payloads (block)")
    conn.commit()

def get_block_data(source_conn: sqlite3.Connection, from_block: int) -> dict:
    entities = source_conn.execute("""
        SELECT DISTINCT entity_key FROM payloads WHERE from_block = ?
    """, (from_block,)).fetchall()
    entity_keys = [e[0] for e in entities]
    if not entity_keys:
        return {"entity_keys": [], "string_attrs": [], "numeric_attrs": [], "payloads": []}
    placeholders = ",".join("?" * len(entity_keys))
    string_attrs = source_conn.execute(f"""
        SELECT entity_key, key, value
        FROM string_attributes
        WHERE entity_key IN ({placeholders}) AND from_block = ?
    """, (*entity_keys, from_block)).fetchall()
    numeric_attrs = source_conn.execute(f"""
        SELECT entity_key, key, value
        FROM numeric_attributes
        WHERE entity_key IN ({placeholders}) AND from_block = ?
    """, (*entity_keys, from_block)).fetchall()
    payloads = source_conn.execute(f"""
        SELECT entity_key, payload, content_type, string_attributes, numeric_attributes
        FROM payloads
        WHERE entity_key IN ({placeholders}) AND from_block = ?
    """, (*entity_keys, from_block)).fetchall()
    return {
        "entity_keys": entity_keys,
        "string_attrs": string_attrs,
        "numeric_attrs": numeric_attrs,
        "payloads": payloads,
    }

def insert_block_data(
    dest_conn: sqlite3.Connection,
    block_data: dict,
    new_block_num: int,
) -> tuple[int, int, int, int, int]:
    key_mapping = {}
    for old_key in block_data["entity_keys"]:
        new_key = uuid.uuid4().bytes
        key_mapping[old_key] = new_key
    cursor = dest_conn.cursor()
    num_attrs = 0
    payload_bytes = 0
    num_str_attrs = 0
    num_num_attrs = 0
    for row in block_data["string_attrs"]:
        old_key, key, value = row
        new_key = key_mapping[old_key]
        cursor.execute(
            "INSERT INTO string_attributes (entity_key, block, key, value) VALUES (?, ?, ?, ?)",
            (new_key, new_block_num, key, value)
        )
        num_attrs += 1
        num_str_attrs += 1
    for row in block_data["numeric_attrs"]:
        old_key, key, value = row
        new_key = key_mapping[old_key]
        cursor.execute(
            "INSERT INTO numeric_attributes (entity_key, block, key, value) VALUES (?, ?, ?, ?)",
            (new_key, new_block_num, key, value)
        )
        num_attrs += 1
        num_num_attrs += 1
    for row in block_data["payloads"]:
        old_key, payload, content_type, str_attrs, num_attrs_json = row
        new_key = key_mapping[old_key]
        cursor.execute(
            """INSERT INTO payloads (entity_key, block, payload, content_type, string_attributes, numeric_attributes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
            (new_key, new_block_num, payload, content_type, str_attrs, num_attrs_json)
        )
        payload_bytes += len(payload) if payload else 0
    return (len(block_data["entity_keys"]), num_attrs, payload_bytes // 1024, num_str_attrs, num_num_attrs)

def get_current_block_count(dest_conn: sqlite3.Connection) -> int:
    try:
        result = dest_conn.execute("SELECT MAX(block) FROM payloads").fetchone()
        return result[0] if result[0] is not None else 0
    except sqlite3.OperationalError:
        return 0

def main():
    if len(sys.argv) != 5:
        print("Usage: python -m db.10_benchmark_sampled_blocks_simple_eav <sample_db> <base_db> <output_db> <num_blocks>")
        print()
        print("Arguments:")
        print("  sample_db  - Source database to sample blocks from (e.g., arkiv-data-mendoza.db)")
        print('  base_db    - Starting database ("" for fresh start, or existing DB path)')
        print("  output_db  - Output database (will be created or appended to)")
        print("  num_blocks - Number of blocks to add")
        sys.exit(1)
    sample_db_path = Path(sys.argv[1])
    base_db_arg = sys.argv[2]
    output_db_path = Path(sys.argv[3])
    num_blocks = int(sys.argv[4])
    csv_path = Path(str(output_db_path) + ".csv")
    if not sample_db_path.exists():
        print(f"Error: Sample database not found: {sample_db_path}")
        sys.exit(1)
    base_db_path = None if base_db_arg == "" else Path(base_db_arg)
    if base_db_path is not None and not base_db_path.exists():
        print(f"Error: Base database not found: {base_db_path}")
        sys.exit(1)
    print("=" * 70)
    print("Sampled Block Benchmark (simple EAV, string/numeric attr counts)")
    print("=" * 70)
    print(f"Sample DB:     {sample_db_path}")
    print(f"Base DB:       {base_db_path if base_db_path else '(fresh start)'}")
    print(f"Output DB:     {output_db_path}")
    print(f"CSV file:      {csv_path}")
    print(f"Blocks to add: {num_blocks}")
    print()
    source_conn = sqlite3.connect(str(sample_db_path))
    source_conn.row_factory = sqlite3.Row
    print("Finding valid blocks (1-500 entities)...")
    valid_blocks = get_valid_blocks(source_conn)
    print(f"Found {len(valid_blocks):,} valid blocks")
    if len(valid_blocks) < num_blocks:
        print(f"Warning: Only {len(valid_blocks)} valid blocks available, reducing sample size")
        num_blocks = len(valid_blocks)
    sampled_blocks = random.sample(valid_blocks, num_blocks)
    print(f"Sampled {num_blocks} blocks")
    print()
    output_db_path.parent.mkdir(parents=True, exist_ok=True)
    if base_db_path is not None and base_db_path != output_db_path:
        print(f"Copying {base_db_path} -> {output_db_path}...")
        shutil.copy2(base_db_path, output_db_path)
        for suffix in ["-wal", "-shm"]:
            src = Path(str(base_db_path) + suffix)
            if src.exists():
                shutil.copy2(src, Path(str(output_db_path) + suffix))
    is_new_db = not output_db_path.exists()
    dest_conn = sqlite3.connect(str(output_db_path))
    dest_conn.execute("PRAGMA journal_mode=WAL")
    dest_conn.execute("PRAGMA synchronous=NORMAL")
    create_simple_eav_schema(dest_conn)
    start_block = get_current_block_count(dest_conn) + 1
    print(f"Starting at block {start_block}")
    write_header = not csv_path.exists() or is_new_db
    csv_file = open(csv_path, "a")
    if write_header:
        csv_file.write("block_nr,num_entities,num_attributes,num_string_attrs,num_numeric_attrs,payload_kb,commit_time_ms,db_size_kb\n")
    print()
    print("Inserting blocks...")
    print("-" * 70)
    total_entities = 0
    total_attrs = 0
    total_str_attrs = 0
    total_num_attrs = 0
    total_payload_kb = 0
    total_time_ms = 0
    for i, (source_block, entity_count) in enumerate(sampled_blocks):
        new_block_num = start_block + i
        block_data = get_block_data(source_conn, source_block)
        start_time = time.perf_counter()
        num_entities, num_attrs, payload_kb, num_str_attrs, num_num_attrs = insert_block_data(dest_conn, block_data, new_block_num)
        dest_conn.commit()
        end_time = time.perf_counter()
        commit_time_ms = int((end_time - start_time) * 1000)
        db_size_kb = output_db_path.stat().st_size // 1024
        csv_file.write(f"{new_block_num},{num_entities},{num_attrs},{num_str_attrs},{num_num_attrs},{payload_kb},{commit_time_ms},{db_size_kb}\n")
        csv_file.flush()
        total_entities += num_entities
        total_attrs += num_attrs
        total_str_attrs += num_str_attrs
        total_num_attrs += num_num_attrs
        total_payload_kb += payload_kb
        total_time_ms += commit_time_ms
        if (i + 1) % 100 == 0 or i == num_blocks - 1:
            print(f"  Block {new_block_num}: {i+1}/{num_blocks} done, DB size: {db_size_kb/1024:.1f} MB")
    csv_file.close()
    dest_conn.close()
    source_conn.close()
    final_db_size_kb = output_db_path.stat().st_size // 1024
    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"Blocks inserted:    {num_blocks:,}")
    print(f"Total entities:     {total_entities:,}")
    print(f"Total attributes:   {total_attrs:,}")
    print(f"  String attrs:     {total_str_attrs:,}")
    print(f"  Numeric attrs:    {total_num_attrs:,}")
    print(f"Total payload:      {total_payload_kb/1024:.1f} MB")
    print(f"Total time:         {total_time_ms/1000:.1f} s")
    print(f"Final DB size:      {final_db_size_kb/1024:.1f} MB")
    print()
    print(f"Avg entities/block: {total_entities/num_blocks:.1f}")
    print(f"Avg attrs/block:    {total_attrs/num_blocks:.1f}")
    print(f"  String/block:     {total_str_attrs/num_blocks:.1f}")
    print(f"  Numeric/block:    {total_num_attrs/num_blocks:.1f}")
    print(f"Avg time/block:     {total_time_ms/num_blocks:.1f} ms")
    print(f"Entities/second:    {total_entities/(total_time_ms/1000):.0f}")
    print("=" * 70)
    print()
    print(f"CSV file: {csv_path}")

if __name__ == "__main__":
    main()
