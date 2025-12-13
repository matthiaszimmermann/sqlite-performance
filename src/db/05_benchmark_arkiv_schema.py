#!/usr/bin/env python3
"""Benchmark script using the exact arkiv schema.

This script measures insert performance with the full arkiv schema:
- string_attributes table (6 indexes)
- numeric_attributes table (5 indexes)
- payloads table (2 indexes)

Total: 13 indexes across 3 tables per transaction.
"""

import sqlite3
import time
import tempfile
import json
import random
from dataclasses import dataclass
from pathlib import Path

NUM_ENTITIES = 100_000  # Number of entities to insert
ENTITIES_PER_BLOCK = 100  # Entities per block (commit)
BLOCK_START = 1


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run."""
    name: str
    num_entities: int
    num_string_attrs: int
    num_numeric_attrs: int
    num_payloads: int
    duration_seconds: float
    
    @property
    def entities_per_second(self) -> float:
        return self.num_entities / self.duration_seconds
    
    @property
    def total_rows(self) -> int:
        return self.num_string_attrs + self.num_numeric_attrs + self.num_payloads


def create_arkiv_schema(conn: sqlite3.Connection) -> None:
    """Create the exact arkiv schema with all tables and indexes."""
    
    # string_attributes table (6 indexes including PK)
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
    conn.execute("""
        CREATE INDEX string_attributes_entity_key_value_index 
        ON string_attributes (from_block, to_block, key, value)
    """)
    conn.execute("""
        CREATE INDEX string_attributes_kv_temporal_idx 
        ON string_attributes (key, value, from_block DESC, to_block DESC)
    """)
    conn.execute("""
        CREATE INDEX string_attributes_entity_key_index 
        ON string_attributes (from_block, to_block, key)
    """)
    conn.execute("""
        CREATE INDEX string_attributes_delete_index 
        ON string_attributes (to_block)
    """)
    conn.execute("""
        CREATE INDEX string_attributes_entity_kv_idx 
        ON string_attributes (entity_key, key, from_block DESC)
    """)
    
    # numeric_attributes table (5 indexes including PK)
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
    conn.execute("""
        CREATE INDEX numeric_attributes_entity_key_value_index 
        ON numeric_attributes (from_block, to_block, key, value)
    """)
    conn.execute("""
        CREATE INDEX numeric_attributes_entity_key_index 
        ON numeric_attributes (from_block, to_block, key)
    """)
    conn.execute("""
        CREATE INDEX numeric_attributes_kv_temporal_idx 
        ON numeric_attributes (key, value, from_block DESC, to_block DESC)
    """)
    conn.execute("""
        CREATE INDEX numeric_attributes_delete_index 
        ON numeric_attributes (to_block)
    """)
    
    # payloads table (2 indexes including PK)
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
    conn.execute("""
        CREATE INDEX payloads_entity_key_index 
        ON payloads (entity_key, from_block, to_block)
    """)
    conn.execute("""
        CREATE INDEX payloads_delete_index 
        ON payloads (to_block)
    """)
    
    # last_block table
    conn.execute("""
        CREATE TABLE last_block (
            id INTEGER NOT NULL DEFAULT 1 CHECK (id = 1),
            block INTEGER NOT NULL,
            PRIMARY KEY (id)
        )
    """)


# Pool of possible attribute keys (simulates variety across many projects)
NUM_ATTR_KEYS = 500


def create_string_attributes(n: int, seed: int = 42) -> dict[str, str]:
    """Create n string attributes with random values.
    
    Args:
        n: Number of string attributes to create
        seed: Random seed for reproducibility
        
    Returns:
        Dictionary of attribute key -> value
    """
    random.seed(seed)
    attrs = {}
    for _ in range(n):
        # Sample key name randomly from the pool
        key = f"str_attr_{random.randint(1, NUM_ATTR_KEYS)}"
        value = f"value_{random.randint(0, 100000)}"
        attrs[key] = value
    return attrs


def create_numeric_attributes(n: int, seed: int = 42) -> dict[str, int]:
    """Create n numeric attributes with random values.
    
    Args:
        n: Number of numeric attributes to create
        seed: Random seed for reproducibility
        
    Returns:
        Dictionary of attribute key -> value
    """
    random.seed(seed)
    attrs = {}
    for _ in range(n):
        # Sample key name randomly from the pool
        key = f"num_attr_{random.randint(1, NUM_ATTR_KEYS)}"
        value = random.randint(0, 1000000)
        attrs[key] = value
    return attrs


def insert_entity(
    cursor: sqlite3.Cursor,
    entity_key: bytes,
    from_block: int,
    to_block: int,
    payload: bytes,
    string_attributes: dict[str, str],
    numeric_attributes: dict[str, int],
    content_type: str = "application/octet-stream",
) -> tuple[int, int, int]:
    """Insert an entity with all its attributes into the arkiv schema.
    
    Args:
        cursor: Database cursor
        entity_key: Unique entity identifier (BLOB)
        from_block: Block number when entity becomes valid
        to_block: Block number when entity expires (999999999 for active)
        payload: Entity payload data
        string_attributes: Dictionary of string attribute key -> value
        numeric_attributes: Dictionary of numeric attribute key -> value
        content_type: MIME type of payload
        
    Returns:
        Tuple of (num_string_attrs, num_numeric_attrs, num_payloads) inserted
    """
    # Insert string attributes
    str_insert_sql = """
        INSERT INTO string_attributes (entity_key, from_block, to_block, key, value)
        VALUES (?, ?, ?, ?, ?)
    """
    for key, value in string_attributes.items():
        cursor.execute(str_insert_sql, (entity_key, from_block, to_block, key, value))
    
    # Insert numeric attributes
    num_insert_sql = """
        INSERT INTO numeric_attributes (entity_key, from_block, to_block, key, value)
        VALUES (?, ?, ?, ?, ?)
    """
    for key, value in numeric_attributes.items():
        cursor.execute(num_insert_sql, (entity_key, from_block, to_block, key, value))
    
    # Insert payload with embedded attributes as JSON
    payload_insert_sql = """
        INSERT INTO payloads (entity_key, from_block, to_block, payload, content_type, 
                              string_attributes, numeric_attributes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    cursor.execute(payload_insert_sql, (
        entity_key,
        from_block,
        to_block,
        payload,
        content_type,
        json.dumps(string_attributes),
        json.dumps(numeric_attributes),
    ))
    
    return (len(string_attributes), len(numeric_attributes), 1)


def run_benchmark(
    num_entities: int,
    entities_per_block: int,
    num_str_attrs: int,
    num_int_attrs: int,
    payload_size: int = 256,
) -> BenchmarkResult:
    """Run benchmark inserting entities with the full arkiv schema.
    
    Args:
        num_entities: Total number of entities to insert
        entities_per_block: Number of entities per block (commit)
        num_str_attrs: Number of string attributes per entity
        num_int_attrs: Number of numeric attributes per entity
        payload_size: Size of payload blob in bytes
    """
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    total_str_attrs = 0
    total_num_attrs = 0
    total_payloads = 0
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        create_arkiv_schema(conn)
        
        cursor = conn.cursor()
        start = time.perf_counter()
        
        block_num = BLOCK_START
        entity_count = 0
        
        while entity_count < num_entities:
            # Process one block
            block_entity_count = min(entities_per_block, num_entities - entity_count)
            
            for i in range(block_entity_count):
                entity_id = entity_count + i
                entity_key = f"entity_{entity_id:08d}".encode()
                
                # Create attributes with varying seeds for variety
                str_attrs = create_string_attributes(num_str_attrs, seed=entity_id)
                int_attrs = create_numeric_attributes(num_int_attrs, seed=entity_id + 1000000)
                
                # Create payload
                payload = bytes([random.randint(0, 255) for _ in range(payload_size)])
                
                # Insert entity
                s, n, p = insert_entity(
                    cursor,
                    entity_key,
                    from_block=block_num,
                    to_block=999999999,
                    payload=payload,
                    string_attributes=str_attrs,
                    numeric_attributes=int_attrs,
                )
                total_str_attrs += s
                total_num_attrs += n
                total_payloads += p
            
            conn.commit()
            entity_count += block_entity_count
            block_num += 1
        
        end = time.perf_counter()
        conn.close()
        
        return BenchmarkResult(
            name=f"Arkiv schema ({num_str_attrs} str + {num_int_attrs} int attrs)",
            num_entities=num_entities,
            num_string_attrs=total_str_attrs,
            num_numeric_attrs=total_num_attrs,
            num_payloads=total_payloads,
            duration_seconds=end - start,
        )
    finally:
        Path(db_path).unlink(missing_ok=True)
        Path(db_path + "-wal").unlink(missing_ok=True)
        Path(db_path + "-shm").unlink(missing_ok=True)


def print_result(result: BenchmarkResult) -> None:
    """Print benchmark result."""
    print()
    print("=" * 70)
    print(f"Configuration: {result.name}")
    print("=" * 70)
    print(f"Entities inserted:      {result.num_entities:,}")
    print(f"String attributes:      {result.num_string_attrs:,}")
    print(f"Numeric attributes:     {result.num_numeric_attrs:,}")
    print(f"Payloads:               {result.num_payloads:,}")
    print(f"Total rows:             {result.total_rows:,}")
    print("-" * 70)
    print(f"Duration:               {result.duration_seconds:.3f}s")
    print(f"Entities/sec:           {result.entities_per_second:,.0f}")
    print(f"Rows/sec:               {result.total_rows / result.duration_seconds:,.0f}")
    print("=" * 70)


def main():
    """Run benchmarks."""
    print("SQLite Arkiv Schema Benchmark")
    print("=" * 70)
    print(f"Entities: {NUM_ENTITIES:,}")
    print(f"Entities per block: {ENTITIES_PER_BLOCK}")
    print(f"Schema: Full arkiv (13 indexes across 3 tables)")
    print()
    
    # Typical arkiv scenario: ~5 string attrs, ~3 numeric attrs per entity
    # Payload sizes vary by entity complexity
    configs = [
        (5, 3, 5 * 1024, "Typical arkiv entity"),      # 5KB payload
        (10, 5, 50 * 1024, "Larger entity"),           # 50KB payload
        (2, 1, 1 * 1024, "Minimal entity"),            # 1KB payload
    ]
    
    print("Running benchmarks...")
    print("-" * 40)
    
    results = []
    for num_str, num_int, payload_sz, desc in configs:
        print(f"  Testing: {desc} ({num_str} str + {num_int} int, {payload_sz//1024}KB)...", end=" ", flush=True)
        result = run_benchmark(
            num_entities=NUM_ENTITIES,
            entities_per_block=ENTITIES_PER_BLOCK,
            num_str_attrs=num_str,
            num_int_attrs=num_int,
            payload_size=payload_sz,
        )
        results.append(result)
        print(f"{result.duration_seconds:.3f}s ({result.entities_per_second:,.0f} ent/sec)")
    
    # Print detailed results
    for result in results:
        print_result(result)
    
    # Summary comparison
    print()
    print("Summary")
    print("-" * 70)
    print(f"{'Config':<30} {'Entities/s':<12} {'Rows/s':<12} {'Total Rows':<12}")
    print("-" * 70)
    for r in results:
        rows_per_sec = r.total_rows / r.duration_seconds
        print(f"{r.name:<30} {r.entities_per_second:<12,.0f} {rows_per_sec:<12,.0f} {r.total_rows:<12,}")


if __name__ == "__main__":
    main()
