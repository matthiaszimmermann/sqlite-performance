#!/usr/bin/env python3
"""Benchmark SQLite insert performance at current memory allocation.

Run this ONCE per Docker memory configuration to measure performance.
Then change Docker memory limit and run again.

Experiment sequence:
1. Set Docker Desktop memory to 8 GB  → run this script → record result
2. Set Docker Desktop memory to 12 GB → run this script → record result  
3. Set Docker Desktop memory to 16 GB → run this script → record result
4. Set Docker Desktop memory to 20 GB → run this script → record result

The script auto-configures SQLite cache to 50% of available RAM.

Usage:
    uv run python -m db.07_benchmark_memory              # default: benchmark_memory_test.db
    uv run python -m db.07_benchmark_memory 8gb          # creates: benchmark_memory_test_8gb.db
    uv run python -m db.07_benchmark_memory 16gb         # creates: benchmark_memory_test_16gb.db
"""

import sqlite3
import sys
import time
import json
import random
from pathlib import Path

# Test parameters
NUM_ENTITIES = 50_000  # Fewer entities but larger payloads
ENTITIES_PER_BLOCK = 100
PAYLOAD_SIZE = 50 * 1024  # 50KB - realistic large payload (this is the dominant factor!)
NUM_STR_ATTRS = 10
NUM_INT_ATTRS = 7
NUM_ATTR_KEYS = 500

# Database base path
DB_BASE_PATH = Path("/workspaces/sqlite-performance/data")
DB_BASE_NAME = "benchmark_memory_test"


def get_db_path(postfix: str | None = None) -> Path:
    """Get database path with optional postfix."""
    if postfix:
        return DB_BASE_PATH / f"{DB_BASE_NAME}_{postfix}.db"
    return DB_BASE_PATH / f"{DB_BASE_NAME}.db"


def get_memory_info() -> tuple[float, float]:
    """Get total and available system memory in GB."""
    total = available = 0.0
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemTotal:'):
                total = int(line.split()[1]) / 1024 / 1024
            elif line.startswith('MemAvailable:'):
                available = int(line.split()[1]) / 1024 / 1024
    return total, available


def create_arkiv_schema(conn: sqlite3.Connection) -> None:
    """Create the full arkiv schema (bi-temporal, 13 indexes)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS string_attributes (
            entity_key BLOB NOT NULL,
            from_block INTEGER NOT NULL,
            to_block INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (entity_key, key, from_block)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS sa_ekv_idx ON string_attributes (from_block, to_block, key, value)")
    conn.execute("CREATE INDEX IF NOT EXISTS sa_kv_idx ON string_attributes (key, value, from_block DESC, to_block DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS sa_ek_idx ON string_attributes (from_block, to_block, key)")
    conn.execute("CREATE INDEX IF NOT EXISTS sa_del_idx ON string_attributes (to_block)")
    conn.execute("CREATE INDEX IF NOT EXISTS sa_ekv2_idx ON string_attributes (entity_key, key, from_block DESC)")
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS numeric_attributes (
            entity_key BLOB NOT NULL,
            from_block INTEGER NOT NULL,
            to_block INTEGER NOT NULL,
            key TEXT NOT NULL,
            value INTEGER NOT NULL,
            PRIMARY KEY (entity_key, key, from_block)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS na_ekv_idx ON numeric_attributes (from_block, to_block, key, value)")
    conn.execute("CREATE INDEX IF NOT EXISTS na_ek_idx ON numeric_attributes (from_block, to_block, key)")
    conn.execute("CREATE INDEX IF NOT EXISTS na_kv_idx ON numeric_attributes (key, value, from_block DESC, to_block DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS na_del_idx ON numeric_attributes (to_block)")
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payloads (
            entity_key BLOB NOT NULL,
            from_block INTEGER NOT NULL,
            to_block INTEGER NOT NULL,
            payload BLOB NOT NULL,
            content_type TEXT NOT NULL DEFAULT '',
            string_attributes TEXT NOT NULL DEFAULT '{}',
            numeric_attributes TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (entity_key, from_block)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS p_ek_idx ON payloads (entity_key, from_block, to_block)")
    conn.execute("CREATE INDEX IF NOT EXISTS p_del_idx ON payloads (to_block)")


def create_string_attributes(n: int, seed: int) -> dict[str, str]:
    random.seed(seed)
    return {f"str_attr_{random.randint(1, NUM_ATTR_KEYS)}": f"value_{random.randint(0, 100000)}" 
            for _ in range(n)}


def create_numeric_attributes(n: int, seed: int) -> dict[str, int]:
    random.seed(seed)
    return {f"num_attr_{random.randint(1, NUM_ATTR_KEYS)}": random.randint(0, 1000000) 
            for _ in range(n)}


def insert_entity(cursor, entity_key: bytes, from_block: int, payload: bytes,
                  str_attrs: dict, int_attrs: dict) -> int:
    to_block = 999999999
    rows = 0
    
    for key, value in str_attrs.items():
        cursor.execute(
            "INSERT INTO string_attributes (entity_key, from_block, to_block, key, value) VALUES (?, ?, ?, ?, ?)",
            (entity_key, from_block, to_block, key, value))
        rows += 1
    
    for key, value in int_attrs.items():
        cursor.execute(
            "INSERT INTO numeric_attributes (entity_key, from_block, to_block, key, value) VALUES (?, ?, ?, ?, ?)",
            (entity_key, from_block, to_block, key, value))
        rows += 1
    
    cursor.execute(
        """INSERT INTO payloads (entity_key, from_block, to_block, payload, content_type, 
           string_attributes, numeric_attributes) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (entity_key, from_block, to_block, payload, "application/octet-stream",
         json.dumps(str_attrs), json.dumps(int_attrs)))
    rows += 1
    
    return rows


def run_benchmark(cache_size_mb: int, db_path: Path) -> dict:
    """Run the benchmark and return results."""
    # Clean up any existing test database
    db_path.unlink(missing_ok=True)
    Path(str(db_path) + "-wal").unlink(missing_ok=True)
    Path(str(db_path) + "-shm").unlink(missing_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    
    # Set cache size
    cache_kb = cache_size_mb * 1024
    conn.execute(f"PRAGMA cache_size = -{cache_kb}")
    actual_cache = conn.execute("PRAGMA cache_size").fetchone()[0]
    
    create_arkiv_schema(conn)
    cursor = conn.cursor()
    
    # Run benchmark
    total_rows = 0
    block_num = 1
    entity_count = 0
    
    start = time.perf_counter()
    
    while entity_count < NUM_ENTITIES:
        block_size = min(ENTITIES_PER_BLOCK, NUM_ENTITIES - entity_count)
        
        for i in range(block_size):
            entity_id = entity_count + i
            entity_key = f"entity_{entity_id:08d}".encode()
            
            str_attrs = create_string_attributes(NUM_STR_ATTRS, seed=entity_id)
            int_attrs = create_numeric_attributes(NUM_INT_ATTRS, seed=entity_id + 1000000)
            payload = bytes([random.randint(0, 255) for _ in range(PAYLOAD_SIZE)])
            
            total_rows += insert_entity(cursor, entity_key, block_num, payload, str_attrs, int_attrs)
        
        conn.commit()
        entity_count += block_size
        block_num += 1
        
        # Progress indicator
        if entity_count % 10000 == 0:
            print(f"    {entity_count:,} / {NUM_ENTITIES:,} entities...", flush=True)
    
    end = time.perf_counter()
    duration = end - start
    
    # Get final stats
    db_size_mb = db_path.stat().st_size / 1024 / 1024
    
    conn.close()
    
    return {
        "cache_size_mb": cache_size_mb,
        "actual_cache_pages": actual_cache,
        "num_entities": NUM_ENTITIES,
        "total_rows": total_rows,
        "duration_seconds": duration,
        "entities_per_second": NUM_ENTITIES / duration,
        "rows_per_second": total_rows / duration,
        "db_size_mb": db_size_mb,
    }


def main():
    # Parse optional postfix argument
    postfix = sys.argv[1] if len(sys.argv) > 1 else None
    db_path = get_db_path(postfix)
    
    total_mem, avail_mem = get_memory_info()
    
    print("=" * 70)
    print("SQLite Memory Benchmark")
    print("=" * 70)
    print(f"Docker Memory:     {total_mem:.1f} GB total, {avail_mem:.1f} GB available")
    print(f"Test:              {NUM_ENTITIES:,} entities, {PAYLOAD_SIZE // 1024}KB payloads")
    print(f"                   {NUM_STR_ATTRS} string + {NUM_INT_ATTRS} numeric attributes each")
    print(f"Schema:            Full arkiv bi-temporal (13 indexes)")
    print()
    
    # Auto-configure cache to ~50% of available RAM
    cache_size_mb = int(avail_mem * 1024 * 0.5)
    cache_size_mb = max(100, min(cache_size_mb, 8000))  # Clamp between 100MB and 8GB
    
    print(f"SQLite cache_size: {cache_size_mb:,} MB (50% of available RAM)")
    print(f"Database path:     {db_path}")
    print()
    print("Running benchmark...")
    print("-" * 70)
    
    result = run_benchmark(cache_size_mb, db_path)
    
    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"Docker Memory:     {total_mem:.1f} GB")
    print(f"SQLite Cache:      {result['cache_size_mb']:,} MB")
    print(f"Duration:          {result['duration_seconds']:.1f} seconds")
    print(f"Entities/second:   {result['entities_per_second']:,.0f}")
    print(f"Rows/second:       {result['rows_per_second']:,.0f}")
    print(f"Database size:     {result['db_size_mb']:.1f} MB")
    print("=" * 70)
    print()
    print(">>> Copy this line for your results table:")
    print(f"| {total_mem:.0f} GB | {cache_size_mb:,} MB | {result['duration_seconds']:.1f}s | {result['entities_per_second']:,.0f} ent/s | {result['rows_per_second']:,.0f} rows/s |")
    print()
    print("Next steps:")
    print("  1. Record this result")
    print("  2. Change Docker Desktop memory limit")
    print("  3. Rebuild/restart devcontainer")
    print("  4. Run this script again")


if __name__ == "__main__":
    main()
