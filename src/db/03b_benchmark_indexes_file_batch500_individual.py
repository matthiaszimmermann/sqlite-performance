#!/usr/bin/env python3
"""Benchmark script comparing insert performance with different index configurations.

This script measures the impact of indexes on insert performance by testing:
1. Table with no indexes (baseline)
2. Simple EVA with 2 indexes
3. Bi-temporal EVA with 6 indexes (similar to arkiv schema)

Uses file-based databases with realistic batch size for arkiv context:
- ~100 entities per block
- ~5 attributes per entity
- = 500 attribute inserts per transaction (block)

This variant uses INDIVIDUAL INSERTS (not executemany) to match arkiv's
actual insert pattern more closely.
"""

import sqlite3
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path

NUM_ROWS = 10_000_000
BATCH_SIZE = 500  # Realistic batch size: ~500 attributes per block (arkiv context)


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run."""
    name: str
    num_indexes: int
    num_inserts: int
    duration_seconds: float
    
    @property
    def inserts_per_second(self) -> float:
        return self.num_inserts / self.duration_seconds
    
    @property
    def ms_per_insert(self) -> float:
        return (self.duration_seconds * 1000) / self.num_inserts


def create_table_no_indexes(conn: sqlite3.Connection) -> None:
    """Create table with no indexes (reference baseline)."""
    conn.execute("""
        CREATE TABLE test_attributes (
            entity_key BLOB NOT NULL,
            from_block INTEGER NOT NULL,
            to_block INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL
        )
    """)


def create_table_simple_eva(conn: sqlite3.Connection) -> None:
    """Create table with 2 indexes (simple EVA pattern)."""
    conn.execute("""
        CREATE TABLE test_attributes (
            entity_key BLOB NOT NULL,
            from_block INTEGER NOT NULL,
            to_block INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (entity_key, key, from_block)
        )
    """)
    conn.execute("CREATE INDEX idx_entity_lookup ON test_attributes (entity_key, key)")


def create_table_temporal_eva(conn: sqlite3.Connection) -> None:
    """Create table matching arkiv bi-temporal schema (6 indexes)."""
    conn.execute("""
        CREATE TABLE test_attributes (
            entity_key BLOB NOT NULL,
            from_block INTEGER NOT NULL,
            to_block INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (entity_key, key, from_block)
        )
    """)
    conn.execute("""
        CREATE INDEX idx_entity_key_value 
        ON test_attributes (from_block, to_block, key, value)
    """)
    conn.execute("""
        CREATE INDEX idx_kv_temporal 
        ON test_attributes (key, value, from_block DESC, to_block DESC)
    """)
    conn.execute("""
        CREATE INDEX idx_entity_key 
        ON test_attributes (from_block, to_block, key)
    """)
    conn.execute("""
        CREATE INDEX idx_delete 
        ON test_attributes (to_block)
    """)
    conn.execute("""
        CREATE INDEX idx_entity_kv 
        ON test_attributes (entity_key, key, from_block DESC)
    """)


def generate_batch(start_idx: int, batch_size: int, seed: int = 42) -> list[tuple]:
    """Generate a batch of test data (memory efficient)."""
    import random
    random.seed(seed + start_idx)  # Reproducible but different per batch
    
    data = []
    for i in range(start_idx, start_idx + batch_size):
        entity_key = f"entity_{i % 1000}".encode()
        from_block = i
        to_block = 999999999
        key = f"attr_{i % 10}"
        value = f"value_{random.randint(0, 10000)}"
        data.append((entity_key, from_block, to_block, key, value))
    
    return data


def run_benchmark(
    name: str,
    create_table_func,
    num_rows: int,
    num_indexes: int,
) -> BenchmarkResult:
    """Run a single benchmark with the given table configuration using file-based DB."""
    # Use temp file for database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")  # Better write performance
        conn.execute("PRAGMA synchronous=NORMAL")
        create_table_func(conn)
        
        insert_sql = """
            INSERT INTO test_attributes (entity_key, from_block, to_block, key, value)
            VALUES (?, ?, ?, ?, ?)
        """
        
        cursor = conn.cursor()
        start = time.perf_counter()
        
        # Insert with individual execute() calls (arkiv-style)
        for batch_start in range(0, num_rows, BATCH_SIZE):
            batch = generate_batch(batch_start, min(BATCH_SIZE, num_rows - batch_start))
            for row in batch:
                cursor.execute(insert_sql, row)
            conn.commit()
        
        end = time.perf_counter()
        conn.close()
        
        return BenchmarkResult(
            name=name,
            num_indexes=num_indexes,
            num_inserts=num_rows,
            duration_seconds=end - start,
        )
    finally:
        # Clean up temp file
        Path(db_path).unlink(missing_ok=True)
        Path(db_path + "-wal").unlink(missing_ok=True)
        Path(db_path + "-shm").unlink(missing_ok=True)


def print_results(results: list[BenchmarkResult], baseline: BenchmarkResult) -> None:
    """Print benchmark results in a formatted table."""
    print()
    print("=" * 80)
    print(f"{'Configuration':<30} {'Indexes':<8} {'Time (s)':<10} {'Ins/sec':<12} {'Slowdown':<10}")
    print("=" * 80)
    
    for r in results:
        slowdown = r.duration_seconds / baseline.duration_seconds
        print(
            f"{r.name:<30} {r.num_indexes:<8} {r.duration_seconds:<10.3f} "
            f"{r.inserts_per_second:<12,.0f} {slowdown:<10.2f}x"
        )
    
    print("=" * 80)


def main():
    """Run all benchmarks."""
    print("SQLite Index Performance Benchmark (File-based, Batch=500, Individual Inserts)")
    print("=" * 80)
    
    num_rows = NUM_ROWS
    print(f"Inserting {num_rows:,} rows per test (commit every {BATCH_SIZE:,} rows)")
    print(f"Insert mode: Individual execute() calls (arkiv-style)")
    print()
    
    configs = [
        ("No indexes (reference)", create_table_no_indexes, 0),
        ("Simple EVA (2 indexes)", create_table_simple_eva, 2),
        ("Bi-temporal EVA (6 indexes)", create_table_temporal_eva, 6),
    ]
    
    # Run benchmarks
    print("Running benchmarks...")
    print("-" * 40)
    
    results = []
    for name, create_func, num_idx in configs:
        print(f"  Testing: {name}...", end=" ", flush=True)
        result = run_benchmark(name, create_func, num_rows, num_idx)
        results.append(result)
        print(f"{result.duration_seconds:.3f}s ({result.inserts_per_second:,.0f} ins/sec)")
    
    print_results(results, results[0])
    
    # Summary
    print()
    print("Summary: Simple EVA vs Bi-temporal EVA")
    print("-" * 40)
    simple = results[1]
    temporal = results[2]
    
    slowdown = temporal.duration_seconds / simple.duration_seconds
    
    print(f"Bi-temporal is {slowdown:.2f}x slower than Simple EVA")
    print()


if __name__ == "__main__":
    main()
