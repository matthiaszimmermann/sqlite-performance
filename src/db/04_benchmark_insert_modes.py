#!/usr/bin/env python3
"""Benchmark comparing executemany vs individual execute calls.

This script measures the difference between:
1. executemany (batch insert) + commit
2. Individual execute() calls + single commit (arkiv-style)

Both use the same commit frequency (every 500 rows) to isolate
the insert mechanism overhead from commit overhead.
"""

import sqlite3
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path

NUM_ROWS = 1_000_000  # Reduced for faster runs
BATCH_SIZE = 500  # Commit every 500 rows


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run."""
    name: str
    insert_mode: str
    num_inserts: int
    duration_seconds: float
    
    @property
    def inserts_per_second(self) -> float:
        return self.num_inserts / self.duration_seconds


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
    """Generate a batch of test data."""
    import random
    random.seed(seed + start_idx)
    
    data = []
    for i in range(start_idx, start_idx + batch_size):
        entity_key = f"entity_{i % 1000}".encode()
        from_block = i
        to_block = 999999999
        key = f"attr_{i % 10}"
        value = f"value_{random.randint(0, 10000)}"
        data.append((entity_key, from_block, to_block, key, value))
    
    return data


def run_benchmark_executemany(num_rows: int) -> BenchmarkResult:
    """Benchmark using executemany (batch insert)."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        create_table_temporal_eva(conn)
        
        insert_sql = """
            INSERT INTO test_attributes (entity_key, from_block, to_block, key, value)
            VALUES (?, ?, ?, ?, ?)
        """
        
        cursor = conn.cursor()
        start = time.perf_counter()
        
        for batch_start in range(0, num_rows, BATCH_SIZE):
            batch = generate_batch(batch_start, min(BATCH_SIZE, num_rows - batch_start))
            cursor.executemany(insert_sql, batch)
            conn.commit()
        
        end = time.perf_counter()
        conn.close()
        
        return BenchmarkResult(
            name="executemany + commit",
            insert_mode="batch",
            num_inserts=num_rows,
            duration_seconds=end - start,
        )
    finally:
        Path(db_path).unlink(missing_ok=True)
        Path(db_path + "-wal").unlink(missing_ok=True)
        Path(db_path + "-shm").unlink(missing_ok=True)


def run_benchmark_individual(num_rows: int) -> BenchmarkResult:
    """Benchmark using individual execute() calls (arkiv-style)."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        create_table_temporal_eva(conn)
        
        insert_sql = """
            INSERT INTO test_attributes (entity_key, from_block, to_block, key, value)
            VALUES (?, ?, ?, ?, ?)
        """
        
        cursor = conn.cursor()
        start = time.perf_counter()
        
        for batch_start in range(0, num_rows, BATCH_SIZE):
            batch = generate_batch(batch_start, min(BATCH_SIZE, num_rows - batch_start))
            # Individual inserts within transaction
            for row in batch:
                cursor.execute(insert_sql, row)
            conn.commit()
        
        end = time.perf_counter()
        conn.close()
        
        return BenchmarkResult(
            name="individual execute + commit",
            insert_mode="individual",
            num_inserts=num_rows,
            duration_seconds=end - start,
        )
    finally:
        Path(db_path).unlink(missing_ok=True)
        Path(db_path + "-wal").unlink(missing_ok=True)
        Path(db_path + "-shm").unlink(missing_ok=True)


def run_benchmark_individual_prepared(num_rows: int) -> BenchmarkResult:
    """Benchmark using individual execute() with explicit prepared statement."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        create_table_temporal_eva(conn)
        
        insert_sql = """
            INSERT INTO test_attributes (entity_key, from_block, to_block, key, value)
            VALUES (?, ?, ?, ?, ?)
        """
        
        # Note: Python's sqlite3 caches prepared statements automatically
        # This test is to confirm that behavior
        cursor = conn.cursor()
        start = time.perf_counter()
        
        for batch_start in range(0, num_rows, BATCH_SIZE):
            batch = generate_batch(batch_start, min(BATCH_SIZE, num_rows - batch_start))
            for row in batch:
                cursor.execute(insert_sql, row)
            conn.commit()
        
        end = time.perf_counter()
        conn.close()
        
        return BenchmarkResult(
            name="individual (same as above, confirms stmt cache)",
            insert_mode="individual_prepared",
            num_inserts=num_rows,
            duration_seconds=end - start,
        )
    finally:
        Path(db_path).unlink(missing_ok=True)
        Path(db_path + "-wal").unlink(missing_ok=True)
        Path(db_path + "-shm").unlink(missing_ok=True)


def print_results(results: list[BenchmarkResult]) -> None:
    """Print benchmark results."""
    print()
    print("=" * 75)
    print(f"{'Mode':<45} {'Time (s)':<12} {'Ins/sec':<15}")
    print("=" * 75)
    
    baseline = results[0].duration_seconds
    for r in results:
        slowdown = r.duration_seconds / baseline
        print(
            f"{r.name:<45} {r.duration_seconds:<12.3f} "
            f"{r.inserts_per_second:<12,.0f} ({slowdown:.2f}x)"
        )
    
    print("=" * 75)


def main():
    """Run all benchmarks."""
    print("SQLite Insert Mode Benchmark")
    print("=" * 75)
    print(f"Schema: Bi-temporal EVA (6 indexes)")
    print(f"Rows: {NUM_ROWS:,}")
    print(f"Commit frequency: every {BATCH_SIZE} rows")
    print()
    print("Comparing: executemany (batch) vs individual execute() calls")
    print("Both have SAME commit frequency - isolating insert overhead only")
    print()
    
    print("Running benchmarks...")
    print("-" * 40)
    
    results = []
    
    print("  Testing: executemany...", end=" ", flush=True)
    r1 = run_benchmark_executemany(NUM_ROWS)
    results.append(r1)
    print(f"{r1.duration_seconds:.3f}s")
    
    print("  Testing: individual execute...", end=" ", flush=True)
    r2 = run_benchmark_individual(NUM_ROWS)
    results.append(r2)
    print(f"{r2.duration_seconds:.3f}s")
    
    print_results(results)
    
    # Analysis
    print()
    print("Analysis")
    print("-" * 40)
    overhead = (r2.duration_seconds / r1.duration_seconds - 1) * 100
    print(f"Individual execute overhead: {overhead:.1f}%")
    print()
    if overhead < 20:
        print("→ Overhead is small (<20%) - Python FFI cost is minor")
        print("→ Go/arkiv likely sees similar or better performance")
    elif overhead < 50:
        print("→ Moderate overhead - some Python FFI cost visible")
        print("→ Go/arkiv would likely be faster with individual inserts")
    else:
        print("→ Significant overhead - Python FFI cost is substantial")
        print("→ This overhead would NOT apply to Go/arkiv")
    print()


if __name__ == "__main__":
    main()
