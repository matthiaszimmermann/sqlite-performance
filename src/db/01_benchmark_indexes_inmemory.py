#!/usr/bin/env python3
"""Benchmark script comparing insert performance with different index configurations.

This script measures the impact of indexes on insert performance by testing:
1. Table with no indexes (baseline)
2. Simple EVA with 2 indexes
3. Bi-temporal EVA with 6 indexes (similar to arkiv schema)

All tests use in-memory databases to isolate CPU/index overhead from disk I/O.
For large datasets (>1M rows), use benchmark_indexes_file.py instead.
"""

import sqlite3
import time
from dataclasses import dataclass

NUM_ROWS = 1_000_000


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


def generate_test_data(num_rows: int) -> list[tuple]:
    """Generate test data for inserts."""
    import random
    random.seed(42)  # Reproducible results
    
    data = []
    for i in range(num_rows):
        entity_key = f"entity_{i % 1000}".encode()  # 1000 unique entities
        from_block = i
        to_block = 999999999
        key = f"attr_{i % 10}"  # 10 unique attribute keys
        value = f"value_{random.randint(0, 10000)}"
        data.append((entity_key, from_block, to_block, key, value))
    
    return data


def run_benchmark(
    name: str,
    create_table_func,
    test_data: list[tuple],
    num_indexes: int,
) -> BenchmarkResult:
    """Run a single benchmark with the given table configuration."""
    conn = sqlite3.connect(":memory:")
    create_table_func(conn)
    
    insert_sql = """
        INSERT INTO test_attributes (entity_key, from_block, to_block, key, value)
        VALUES (?, ?, ?, ?, ?)
    """
    
    start = time.perf_counter()
    
    cursor = conn.cursor()
    for row in test_data:
        cursor.execute(insert_sql, row)
    conn.commit()
    
    end = time.perf_counter()
    
    conn.close()
    
    return BenchmarkResult(
        name=name,
        num_indexes=num_indexes,
        num_inserts=len(test_data),
        duration_seconds=end - start,
    )


def run_benchmark_batch(
    name: str,
    create_table_func,
    test_data: list[tuple],
    num_indexes: int,
) -> BenchmarkResult:
    """Run benchmark using executemany (batch insert)."""
    conn = sqlite3.connect(":memory:")
    create_table_func(conn)
    
    insert_sql = """
        INSERT INTO test_attributes (entity_key, from_block, to_block, key, value)
        VALUES (?, ?, ?, ?, ?)
    """
    
    start = time.perf_counter()
    
    cursor = conn.cursor()
    cursor.executemany(insert_sql, test_data)
    conn.commit()
    
    end = time.perf_counter()
    
    conn.close()
    
    return BenchmarkResult(
        name=name,
        num_indexes=num_indexes,
        num_inserts=len(test_data),
        duration_seconds=end - start,
    )


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
    print("SQLite Index Performance Benchmark (In-Memory)")
    print("=" * 80)
    
    num_rows = NUM_ROWS
    print(f"Inserting {num_rows:,} rows per test")
    print()
    
    print("Generating test data...")
    test_data = generate_test_data(num_rows)
    print(f"Generated {len(test_data):,} rows")
    
    configs = [
        ("No indexes (reference)", create_table_no_indexes, 0),
        ("Simple EVA (2 indexes)", create_table_simple_eva, 2),
        ("Bi-temporal EVA (6 indexes)", create_table_temporal_eva, 6),
    ]
    
    # Run individual insert benchmarks
    print()
    print("Running benchmarks (individual inserts)...")
    print("-" * 40)
    
    results = []
    for name, create_func, num_idx in configs:
        print(f"  Testing: {name}...", end=" ", flush=True)
        result = run_benchmark(name, create_func, test_data, num_idx)
        results.append(result)
        print(f"{result.duration_seconds:.3f}s")
    
    print_results(results, results[0])
    
    # Run batch insert benchmarks
    print()
    print("Running benchmarks (batch inserts with executemany)...")
    print("-" * 40)
    
    batch_results = []
    for name, create_func, num_idx in configs:
        print(f"  Testing: {name}...", end=" ", flush=True)
        result = run_benchmark_batch(f"{name} (batch)", create_func, test_data, num_idx)
        batch_results.append(result)
        print(f"{result.duration_seconds:.3f}s")
    
    print_results(batch_results, batch_results[0])
    
    # Summary
    print()
    print("Summary: Simple EVA vs Bi-temporal EVA")
    print("-" * 40)
    simple_individual = results[1]
    temporal_individual = results[2]
    simple_batch = batch_results[1]
    temporal_batch = batch_results[2]
    
    individual_slowdown = temporal_individual.duration_seconds / simple_individual.duration_seconds
    batch_slowdown = temporal_batch.duration_seconds / simple_batch.duration_seconds
    
    print(f"Individual inserts: Bi-temporal is {individual_slowdown:.2f}x slower than Simple EVA")
    print(f"Batch inserts:      Bi-temporal is {batch_slowdown:.2f}x slower than Simple EVA")
    print()


if __name__ == "__main__":
    main()
