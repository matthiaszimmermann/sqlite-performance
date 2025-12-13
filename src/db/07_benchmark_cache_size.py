#!/usr/bin/env python3
"""Benchmark script testing SQLite cache size impact on performance.

This script measures insert performance with different PRAGMA cache_size settings
to understand how memory allocation affects write performance.

Key insight: You DON'T need to change Docker memory limits!
Just set PRAGMA cache_size to control SQLite's internal buffer cache.
The OS will also cache file pages, but SQLite's cache is what matters most.

Usage:
    uv run python -m db.07_benchmark_cache_size           # Run all cache sizes
    uv run python -m db.07_benchmark_cache_size 200 500   # Run specific sizes (MB)
"""

import sqlite3
import sys
import time
import tempfile
import json
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

NUM_ENTITIES = 50_000  # Reduced for faster iteration
ENTITIES_PER_BLOCK = 100
BLOCK_START = 1

# Cache sizes to test (in MB) - these are SQLite internal cache, not Docker RAM
# 2 MB = SQLite default, 200 MB = moderate, 2000 MB = large
CACHE_SIZES_MB = [2, 50, 200, 500, 1000, 2000]


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run."""
    cache_size_mb: int
    num_entities: int
    total_rows: int
    duration_seconds: float
    
    @property
    def entities_per_second(self) -> float:
        return self.num_entities / self.duration_seconds
    
    @property
    def rows_per_second(self) -> float:
        return self.total_rows / self.duration_seconds


def get_system_memory_gb() -> float:
    """Get total system memory in GB."""
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemTotal:'):
                kb = int(line.split()[1])
                return kb / 1024 / 1024
    return 0


def get_available_memory_gb() -> float:
    """Get available system memory in GB."""
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable:'):
                kb = int(line.split()[1])
                return kb / 1024 / 1024
    return 0


def create_arkiv_schema(conn: sqlite3.Connection) -> None:
    """Create the full arkiv schema (bi-temporal, 13 indexes)."""
    
    conn.execute("""
        CREATE TABLE string_attributes (
            entity_key BLOB NOT NULL,
            from_block INTEGER NOT NULL,
            to_block INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (entity_key, key, from_block)
        )
    """)
    conn.execute("CREATE INDEX sa_ekv_idx ON string_attributes (from_block, to_block, key, value)")
    conn.execute("CREATE INDEX sa_kv_idx ON string_attributes (key, value, from_block DESC, to_block DESC)")
    conn.execute("CREATE INDEX sa_ek_idx ON string_attributes (from_block, to_block, key)")
    conn.execute("CREATE INDEX sa_del_idx ON string_attributes (to_block)")
    conn.execute("CREATE INDEX sa_ekv2_idx ON string_attributes (entity_key, key, from_block DESC)")
    
    conn.execute("""
        CREATE TABLE numeric_attributes (
            entity_key BLOB NOT NULL,
            from_block INTEGER NOT NULL,
            to_block INTEGER NOT NULL,
            key TEXT NOT NULL,
            value INTEGER NOT NULL,
            PRIMARY KEY (entity_key, key, from_block)
        )
    """)
    conn.execute("CREATE INDEX na_ekv_idx ON numeric_attributes (from_block, to_block, key, value)")
    conn.execute("CREATE INDEX na_ek_idx ON numeric_attributes (from_block, to_block, key)")
    conn.execute("CREATE INDEX na_kv_idx ON numeric_attributes (key, value, from_block DESC, to_block DESC)")
    conn.execute("CREATE INDEX na_del_idx ON numeric_attributes (to_block)")
    
    conn.execute("""
        CREATE TABLE payloads (
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
    conn.execute("CREATE INDEX p_ek_idx ON payloads (entity_key, from_block, to_block)")
    conn.execute("CREATE INDEX p_del_idx ON payloads (to_block)")


NUM_ATTR_KEYS = 500


def create_string_attributes(n: int, seed: int = 42) -> dict[str, str]:
    random.seed(seed)
    attrs = {}
    for _ in range(n):
        key = f"str_attr_{random.randint(1, NUM_ATTR_KEYS)}"
        value = f"value_{random.randint(0, 100000)}"
        attrs[key] = value
    return attrs


def create_numeric_attributes(n: int, seed: int = 42) -> dict[str, int]:
    random.seed(seed)
    attrs = {}
    for _ in range(n):
        key = f"num_attr_{random.randint(1, NUM_ATTR_KEYS)}"
        value = random.randint(0, 1000000)
        attrs[key] = value
    return attrs


def insert_entity(
    cursor: sqlite3.Cursor,
    entity_key: bytes,
    from_block: int,
    payload: bytes,
    string_attributes: dict[str, str],
    numeric_attributes: dict[str, int],
) -> tuple[int, int, int]:
    to_block = 999999999
    
    str_sql = "INSERT INTO string_attributes (entity_key, from_block, to_block, key, value) VALUES (?, ?, ?, ?, ?)"
    for key, value in string_attributes.items():
        cursor.execute(str_sql, (entity_key, from_block, to_block, key, value))
    
    num_sql = "INSERT INTO numeric_attributes (entity_key, from_block, to_block, key, value) VALUES (?, ?, ?, ?, ?)"
    for key, value in numeric_attributes.items():
        cursor.execute(num_sql, (entity_key, from_block, to_block, key, value))
    
    pay_sql = """INSERT INTO payloads (entity_key, from_block, to_block, payload, content_type, 
                 string_attributes, numeric_attributes) VALUES (?, ?, ?, ?, ?, ?, ?)"""
    cursor.execute(pay_sql, (
        entity_key, from_block, to_block, payload, "application/octet-stream",
        json.dumps(string_attributes), json.dumps(numeric_attributes),
    ))
    
    return (len(string_attributes), len(numeric_attributes), 1)


def run_benchmark(cache_size_mb: int) -> BenchmarkResult:
    """Run benchmark with specified cache size."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    total_rows = 0
    num_str_attrs = 5
    num_int_attrs = 3
    payload_size = 5 * 1024  # 5KB
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        
        # Set cache size (negative = KB)
        cache_kb = cache_size_mb * 1024
        conn.execute(f"PRAGMA cache_size = -{cache_kb}")
        
        # Verify cache size
        result = conn.execute("PRAGMA cache_size").fetchone()[0]
        
        create_arkiv_schema(conn)
        
        cursor = conn.cursor()
        start = time.perf_counter()
        
        block_num = BLOCK_START
        entity_count = 0
        
        while entity_count < NUM_ENTITIES:
            block_entity_count = min(ENTITIES_PER_BLOCK, NUM_ENTITIES - entity_count)
            
            for i in range(block_entity_count):
                entity_id = entity_count + i
                entity_key = f"entity_{entity_id:08d}".encode()
                
                str_attrs = create_string_attributes(num_str_attrs, seed=entity_id)
                int_attrs = create_numeric_attributes(num_int_attrs, seed=entity_id + 1000000)
                payload = bytes([random.randint(0, 255) for _ in range(payload_size)])
                
                s, n, p = insert_entity(cursor, entity_key, block_num, payload, str_attrs, int_attrs)
                total_rows += s + n + p
            
            conn.commit()
            entity_count += block_entity_count
            block_num += 1
        
        end = time.perf_counter()
        conn.close()
        
        return BenchmarkResult(
            cache_size_mb=cache_size_mb,
            num_entities=NUM_ENTITIES,
            total_rows=total_rows,
            duration_seconds=end - start,
        )
    finally:
        Path(db_path).unlink(missing_ok=True)
        Path(db_path + "-wal").unlink(missing_ok=True)
        Path(db_path + "-shm").unlink(missing_ok=True)


def main():
    """Run cache size benchmarks."""
    total_mem = get_system_memory_gb()
    avail_mem = get_available_memory_gb()
    
    print("SQLite Cache Size Benchmark")
    print("=" * 70)
    print(f"System Memory: {total_mem:.1f} GB total, {avail_mem:.1f} GB available")
    print(f"Entities: {NUM_ENTITIES:,} (5 str + 3 int attrs, 5KB payload each)")
    print(f"Schema: Full arkiv bi-temporal (13 indexes)")
    print()
    
    # Use command-line args if provided, otherwise use defaults
    if len(sys.argv) > 1:
        try:
            valid_sizes = [int(arg) for arg in sys.argv[1:]]
            print(f"Testing user-specified cache sizes: {valid_sizes} MB")
        except ValueError:
            print("Usage: python -m db.07_benchmark_cache_size [size_mb ...]")
            print("Example: python -m db.07_benchmark_cache_size 2 200 1000")
            sys.exit(1)
    else:
        # Default: test reasonable range, skip very large if not enough RAM
        max_cache = int(avail_mem * 1024 * 0.7)  # 70% of available
        valid_sizes = [s for s in CACHE_SIZES_MB if s <= max_cache]
        if not valid_sizes:
            valid_sizes = [CACHE_SIZES_MB[0]]
        print(f"Testing cache sizes: {valid_sizes} MB")
        if max_cache < max(CACHE_SIZES_MB):
            print(f"(Skipping sizes > {max_cache} MB to stay within available memory)")
    
    print()
    print("Running benchmarks...")
    print("-" * 70)
    
    results = []
    for cache_mb in valid_sizes:
        print(f"  Cache {cache_mb:,} MB...", end=" ", flush=True)
        result = run_benchmark(cache_mb)
        results.append(result)
        print(f"{result.duration_seconds:.1f}s ({result.entities_per_second:,.0f} ent/s, {result.rows_per_second:,.0f} rows/s)")
    
    # Results table
    print()
    print("=" * 70)
    print(f"{'Cache (MB)':<12} {'Time (s)':<10} {'Entities/s':<12} {'Rows/s':<12} {'Speedup':<10}")
    print("=" * 70)
    
    baseline = results[0].duration_seconds
    for r in results:
        speedup = baseline / r.duration_seconds
        print(f"{r.cache_size_mb:<12,} {r.duration_seconds:<10.1f} {r.entities_per_second:<12,.0f} {r.rows_per_second:<12,.0f} {speedup:<10.2f}x")
    
    print("=" * 70)
    
    # Recommendations
    print()
    print("Key Insights:")
    print("-" * 70)
    print("  • SQLite default cache is only 2 MB - tiny for real workloads")
    print("  • PRAGMA cache_size = -N sets cache to N kilobytes")
    print("  • Example: PRAGMA cache_size = -1000000  → 1 GB cache")
    print()
    print("Recommended settings by available RAM:")
    print("  • 8 GB system RAM:   PRAGMA cache_size = -500000   (500 MB)")
    print("  • 16 GB system RAM:  PRAGMA cache_size = -2000000  (2 GB)")
    print("  • 64+ GB system RAM: PRAGMA cache_size = -8000000  (8 GB)")


if __name__ == "__main__":
    main()
