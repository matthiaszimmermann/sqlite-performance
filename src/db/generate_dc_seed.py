"""
Generate Data Center seed database for controlled benchmark testing.

Creates deterministic Node and Workload entities with configurable scale,
enabling verifiable read/write performance testing.

Usage:
    # Create 2x mendoza scale (~27 GB) with 4 data centers
    uv run python -m src.db.generate_dc_seed \
        --datacenters 4 \
        --nodes-per-dc 100000 \
        --workloads-per-node 5 \
        --payload-size 10000 \
        --output data/dc_seed_2x.db

    # Add DC data to existing mendoza database
    uv run python -m src.db.generate_dc_seed \
        --input data/arkiv-data-mendoza.db \
        --datacenters 2 \
        --nodes-per-dc 100000 \
        --workloads-per-node 5 \
        --output data/mendoza_plus_dc.db

    # Small test run
    uv run python -m src.db.generate_dc_seed \
        --datacenters 1 \
        --nodes-per-dc 1000 \
        --workloads-per-node 2 \
        --output data/dc_test.db
"""

import argparse
import os
import random
import secrets
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator


# =============================================================================
# Configuration & Constants
# =============================================================================

NODE = "node"
WORKLOAD = "workload"

MAX_BLOCK = 9223372036854775807  # Max int64, represents "current" state

# System attributes added by arkiv
SYSTEM_STRING_ATTRS = ["$creator", "$key", "$owner"]
SYSTEM_NUMERIC_ATTRS = ["$createdAtBlock", "$expiration", "$opIndex", "$sequence", "$txIndex"]

DEFAULT_NODE_UPDATES_PER_BLOCK = 60
DEFAULT_WORKLOAD_UPDATES_PER_BLOCK = 600


@dataclass
class NodeEntity:
    """Represents a compute node in a data center."""
    entity_key: bytes
    dc_id: str
    node_id: str
    region: str
    status: str
    vm_type: str
    cpu_count: int
    ram_gb: int
    price_hour: int
    avail_hours: int
    payload: bytes
    block: int
    ttl: int
    tx_index: int = 0
    op_index: int = 0
    sequence: int = 0


@dataclass
class WorkloadEntity:
    """Represents a workload/job in a data center."""
    entity_key: bytes
    dc_id: str
    workload_id: str
    status: str
    assigned_node: str
    region: str
    vm_type: str
    req_cpu: int
    req_ram: int
    max_hours: int
    payload: bytes
    block: int
    ttl: int
    tx_index: int = 0
    op_index: int = 0
    sequence: int = 0


# =============================================================================
# Distribution Helpers (encapsulated for easy modification)
# =============================================================================

def get_region_distribution() -> list[tuple[str, float]]:
    """Region distribution: (value, cumulative_probability)."""
    return [
        ("eu-west", 0.40),
        ("us-east", 0.75),   # 0.40 + 0.35
        ("asia-pac", 1.00),  # 0.75 + 0.25
    ]


def get_vm_type_distribution() -> list[tuple[str, float]]:
    """VM type distribution: (value, cumulative_probability)."""
    return [
        ("cpu", 0.70),
        ("gpu", 0.95),       # 0.70 + 0.25
        ("gpu_large", 1.00), # 0.95 + 0.05
    ]


def get_node_status_distribution() -> list[tuple[str, float]]:
    """Node status distribution: (value, cumulative_probability)."""
    return [
        ("available", 0.70),
        ("busy", 0.95),      # 0.70 + 0.25
        ("offline", 1.00),   # 0.95 + 0.05
    ]


def get_workload_status_distribution() -> list[tuple[str, float]]:
    """Workload status distribution: (value, cumulative_probability)."""
    return [
        ("pending", 0.15),
        ("running", 0.95),   # 0.15 + 0.80
        ("completed", 1.00), # 0.95 + 0.05
    ]


def get_cpu_count_distribution() -> list[tuple[int, float]]:
    """CPU count distribution: (value, cumulative_probability)."""
    return [
        (4, 0.30),
        (8, 0.60),
        (16, 0.85),
        (32, 1.00),
    ]


def get_ram_gb_distribution() -> list[tuple[int, float]]:
    """RAM GB distribution: (value, cumulative_probability)."""
    return [
        (16, 0.25),
        (32, 0.55),
        (64, 0.85),
        (128, 1.00),
    ]


def get_price_hour_range() -> tuple[int, int]:
    """Price per hour range in cents: (min, max)."""
    return (50, 500)


def get_avail_hours_distribution() -> list[tuple[int, float]]:
    """Availability hours distribution: (value, cumulative_probability)."""
    return [
        (1, 0.10),
        (4, 0.30),
        (8, 0.55),
        (24, 0.80),
        (168, 1.00),  # 1 week
    ]


def get_req_cpu_distribution() -> list[tuple[int, float]]:
    """Requested CPU distribution: (value, cumulative_probability)."""
    return [
        (1, 0.40),
        (2, 0.70),
        (4, 0.90),
        (8, 1.00),
    ]


def get_req_ram_distribution() -> list[tuple[int, float]]:
    """Requested RAM distribution: (value, cumulative_probability)."""
    return [
        (4, 0.35),
        (8, 0.65),
        (16, 0.90),
        (32, 1.00),
    ]


def get_max_hours_distribution() -> list[tuple[int, float]]:
    """Max runtime hours distribution: (value, cumulative_probability)."""
    return [
        (1, 0.30),
        (2, 0.55),
        (4, 0.75),
        (8, 0.90),
        (24, 1.00),
    ]


def get_ttl_blocks_distribution() -> list[tuple[tuple[int, int], float]]:
    """
    TTL in number of blocks distribution: ((min, max), cumulative_probability).
    
    Block time = 2s, so:
    - 1 hour = 1,800 blocks
    - 1 day = 43,200 blocks
    - 1 week = 302,400 blocks
    
    Distribution:
    - 10%: 1-6 hours (short-lived)
    - 60%: 12 hours - 7 days (medium-lived)
    - 30%: 7-28 days (long-lived)
    """
    return [
        ((1800, 10800), 0.10),       # 1-6 hours
        ((21600, 302400), 0.70),     # 12 hours - 7 days
        ((302400, 1209600), 1.00),   # 7-28 days
    ]


def sample_ttl_blocks(rng: random.Random) -> int:
    """Sample TTL in blocks from the TTL distribution."""
    dist = get_ttl_blocks_distribution()
    r = rng.random()
    for (min_val, max_val), cumulative_prob in dist:
        if r <= cumulative_prob:
            return rng.randint(min_val, max_val)
    # Fallback to last range
    min_val, max_val = dist[-1][0]
    return rng.randint(min_val, max_val)


def sample_from_distribution(rng: random.Random, dist: list[tuple[any, float]]) -> any:
    """Sample a value from a cumulative probability distribution."""
    r = rng.random()
    for value, cumulative_prob in dist:
        if r <= cumulative_prob:
            return value
    return dist[-1][0]  # Fallback to last value


# =============================================================================
# ID Generation (deterministic)
# =============================================================================

def make_dc_id(dc_num: int) -> str:
    """Generate data center ID: dc_01, dc_02, ..."""
    return f"dc_{dc_num:02d}"


def make_node_id(dc_num: int, node_num: int) -> str:
    """Generate node ID: node_01_000001, node_01_000002, ..."""
    return f"node_{dc_num:02d}_{node_num:06d}"


def make_workload_id(dc_num: int, workload_num: int) -> str:
    """Generate workload ID: wl_01_000001, wl_01_000002, ..."""
    return f"wl_{dc_num:02d}_{workload_num:06d}"


def make_entity_key(id_string: str, seed: int) -> bytes:
    """Generate deterministic 32-byte entity key from ID string and seed."""
    # Use seed + id_string to generate reproducible key
    rng = random.Random(f"{seed}:{id_string}")
    return bytes(rng.getrandbits(8) for _ in range(32))


def workload_to_node_num(workload_num: int, nodes_per_dc: int) -> int:
    """Map workload number to node number (deterministic assignment)."""
    return (workload_num - 1) % nodes_per_dc + 1


# =============================================================================
# Entity Creation (high-level)
# =============================================================================

def create_node(
    dc_num: int,
    node_num: int,
    payload_size: int,
    block: int,
    seed: int,
) -> NodeEntity:
    """Create a single Node entity with randomized attributes."""
    rng = random.Random(f"{seed}:node:{dc_num}:{node_num}")
    
    dc_id = make_dc_id(dc_num)
    node_id = make_node_id(dc_num, node_num)
    entity_key = make_entity_key(node_id, seed)
    
    # Sample attributes from distributions
    region = sample_from_distribution(rng, get_region_distribution())
    status = sample_from_distribution(rng, get_node_status_distribution())
    vm_type = sample_from_distribution(rng, get_vm_type_distribution())
    cpu_count = sample_from_distribution(rng, get_cpu_count_distribution())
    ram_gb = sample_from_distribution(rng, get_ram_gb_distribution())
    price_min, price_max = get_price_hour_range()
    price_hour = rng.randint(price_min, price_max)
    avail_hours = sample_from_distribution(rng, get_avail_hours_distribution())
    ttl_blocks = sample_ttl_blocks(rng)

    # Generate random payload
    payload = bytes(rng.getrandbits(8) for _ in range(payload_size))
    
    return NodeEntity(
        entity_key=entity_key,
        dc_id=dc_id,
        node_id=node_id,
        region=region,
        status=status,
        vm_type=vm_type,
        cpu_count=cpu_count,
        ram_gb=ram_gb,
        price_hour=price_hour,
        avail_hours=avail_hours,
        payload=payload,
        block=block,
        ttl=ttl_blocks
    )


def create_workload(
    dc_num: int,
    workload_num: int,
    nodes_per_dc: int,
    payload_size: int,
    block: int,
    seed: int,
) -> WorkloadEntity:
    """Create a single Workload entity with randomized attributes."""
    rng = random.Random(f"{seed}:workload:{dc_num}:{workload_num}")
    
    dc_id = make_dc_id(dc_num)
    workload_id = make_workload_id(dc_num, workload_num)
    entity_key = make_entity_key(workload_id, seed)
    
    # Sample attributes from distributions
    status = sample_from_distribution(rng, get_workload_status_distribution())
    region = sample_from_distribution(rng, get_region_distribution())
    vm_type = sample_from_distribution(rng, get_vm_type_distribution())
    req_cpu = sample_from_distribution(rng, get_req_cpu_distribution())
    req_ram = sample_from_distribution(rng, get_req_ram_distribution())
    max_hours = sample_from_distribution(rng, get_max_hours_distribution())
    ttl_blocks = sample_ttl_blocks(rng)

    # Assign to node (only if running, pending workloads are unassigned)
    if status == "running":
        node_num = workload_to_node_num(workload_num, nodes_per_dc)
        assigned_node = make_node_id(dc_num, node_num)
    else:
        assigned_node = ""
    
    # Generate random payload
    payload = bytes(rng.getrandbits(8) for _ in range(payload_size))
    
    return WorkloadEntity(
        entity_key=entity_key,
        dc_id=dc_id,
        workload_id=workload_id,
        status=status,
        assigned_node=assigned_node,
        region=region,
        vm_type=vm_type,
        req_cpu=req_cpu,
        req_ram=req_ram,
        max_hours=max_hours,
        payload=payload,
        block=block,
        ttl=ttl_blocks
    )


# =============================================================================
# Entity Generators (iterate over all entities)
# =============================================================================

def generate_nodes(
    num_datacenters: int,
    nodes_per_dc: int,
    nodes_per_block: int,
    payload_size: int,
    start_block: int,
    seed: int,
) -> Iterator[NodeEntity]:
    """Generate all Node entities across all data centers."""
    node_counter = 0
    current_block = start_block

    for dc_num in range(1, num_datacenters + 1):
        for node_num in range(1, nodes_per_dc + 1):
            yield create_node(dc_num, node_num, payload_size, current_block, seed)

            node_counter += 1
            if node_counter >= nodes_per_block:
                current_block += 1
                node_counter = 0


def generate_workloads(
    num_datacenters: int,
    nodes_per_dc: int,
    workloads_per_node: float,
    workloads_per_block: int,
    payload_size: int,
    start_block: int,
    seed: int,
) -> Iterator[WorkloadEntity]:
    """Generate all Workload entities across all data centers."""
    workloads_per_dc = int(nodes_per_dc * workloads_per_node)
    workload_counter = 0
    current_block = start_block
    
    for dc_num in range(1, num_datacenters + 1):
        for workload_num in range(1, workloads_per_dc + 1):
            yield create_workload(
                dc_num, workload_num, nodes_per_dc, payload_size, current_block, seed
            )

            workload_counter += 1
            if workload_counter >= workloads_per_block:
                current_block += 1
                workload_counter = 0    


# =============================================================================
# SQL Insert Helpers (lowest level)
# =============================================================================

def node_to_sql_inserts(node: NodeEntity, creator_address: str) -> list[tuple[str, tuple]]:
    """
    Convert a Node entity to SQL INSERT statements.
    
    Returns list of (sql, params) tuples for:
    - string_attributes (4 custom + 3 system = 7 rows)
    - numeric_attributes (4 custom + 5 system = 9 rows)
    - payloads (1 row)
    """
    inserts = []
    entity_key = node.entity_key
    block = node.block
    expires_at_block = block + node.ttl
    
    # String attributes
    string_attrs = [
        ("dc_id", node.dc_id),
        ("type", NODE),
        ("node_id", node.node_id),
        ("region", node.region),
        ("status", node.status),
        ("vm_type", node.vm_type),
        # System attributes
        ("$creator", creator_address),
        ("$key", "0x" + entity_key.hex()),
        ("$owner", creator_address),
    ]
    
    for key, value in string_attrs:
        inserts.append((
            """INSERT INTO string_attributes 
               (entity_key, from_block, to_block, key, value) 
               VALUES (?, ?, ?, ?, ?)""",
            (entity_key, block, expires_at_block, key, value)
        ))
    
    # Numeric attributes
    numeric_attrs = [
        ("cpu_count", node.cpu_count),
        ("ram_gb", node.ram_gb),
        ("price_hour", node.price_hour),
        ("avail_hours", node.avail_hours),
        # System attributes
        ("$createdAtBlock", block),
        ("$expiration", expires_at_block),
        ("$opIndex", node.op_index),
        ("$sequence", node.sequence),
        ("$txIndex", node.tx_index),
    ]

    for key, value in numeric_attrs:
        inserts.append((
            """INSERT INTO numeric_attributes 
               (entity_key, from_block, to_block, key, value) 
               VALUES (?, ?, ?, ?, ?)""",
            (entity_key, block, expires_at_block, key, value)
        ))
    
    # Payload with cached attributes as JSON
    string_attrs_json = "{" + ", ".join(f'"{k}": "{v}"' for k, v in string_attrs) + "}"
    numeric_attrs_json = "{" + ", ".join(f'"{k}": {v}' for k, v in numeric_attrs) + "}"
    
    inserts.append((
        """INSERT INTO payloads 
           (entity_key, from_block, to_block, payload, content_type, string_attributes, numeric_attributes) 
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (entity_key, block, expires_at_block, node.payload, "application/octet-stream", 
         string_attrs_json, numeric_attrs_json)
    ))
    
    return inserts


def workload_to_sql_inserts(workload: WorkloadEntity, creator_address: str) -> list[tuple[str, tuple]]:
    """
    Convert a Workload entity to SQL INSERT statements.
    
    Returns list of (sql, params) tuples for:
    - string_attributes (6 custom + 3 system = 9 rows)
    - numeric_attributes (3 custom + 5 system = 8 rows)
    - payloads (1 row)
    """
    inserts = []
    entity_key = workload.entity_key
    block = workload.block
    expires_at_block = block + workload.ttl
    
    # String attributes
    string_attrs = [
        ("dc_id", workload.dc_id),
        ("type", WORKLOAD),
        ("workload_id", workload.workload_id),
        ("status", workload.status),
        ("assigned_node", workload.assigned_node),
        ("region", workload.region),
        ("vm_type", workload.vm_type),
        # System attributes
        ("$creator", creator_address),
        ("$key", "0x" + entity_key.hex()),
        ("$owner", creator_address),
    ]
    
    for key, value in string_attrs:
        inserts.append((
            """INSERT INTO string_attributes 
               (entity_key, from_block, to_block, key, value) 
               VALUES (?, ?, ?, ?, ?)""",
            (entity_key, block, expires_at_block, key, value)
        ))
    
    # Numeric attributes
    numeric_attrs = [
        ("req_cpu", workload.req_cpu),
        ("req_ram", workload.req_ram),
        ("max_hours", workload.max_hours),
        # System attributes
        ("$createdAtBlock", block),
        ("$expiration", expires_at_block),
        ("$opIndex", workload.op_index),
        ("$sequence", workload.sequence),
        ("$txIndex", workload.tx_index),
    ]
    
    for key, value in numeric_attrs:
        inserts.append((
            """INSERT INTO numeric_attributes 
               (entity_key, from_block, to_block, key, value) 
               VALUES (?, ?, ?, ?, ?)""",
            (entity_key, block, expires_at_block, key, value)
        ))
    
    # Payload with cached attributes as JSON
    string_attrs_json = "{" + ", ".join(f'"{k}": "{v}"' for k, v in string_attrs) + "}"
    numeric_attrs_json = "{" + ", ".join(f'"{k}": {v}' for k, v in numeric_attrs) + "}"
    
    inserts.append((
        """INSERT INTO payloads 
           (entity_key, from_block, to_block, payload, content_type, string_attributes, numeric_attributes) 
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (entity_key, block, expires_at_block, workload.payload, "application/octet-stream",
         string_attrs_json, numeric_attrs_json)
    ))
    
    return inserts


# =============================================================================
# Database Setup
# =============================================================================

SCHEMA_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS string_attributes (
    entity_key BLOB NOT NULL,
    from_block INTEGER NOT NULL,
    to_block INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (entity_key, key, from_block)
);

CREATE TABLE IF NOT EXISTS numeric_attributes (
    entity_key BLOB NOT NULL,
    from_block INTEGER NOT NULL,
    to_block INTEGER NOT NULL,
    key TEXT NOT NULL,
    value INTEGER NOT NULL,
    PRIMARY KEY (entity_key, key, from_block)
);

CREATE TABLE IF NOT EXISTS payloads (
    entity_key BLOB NOT NULL,
    from_block INTEGER NOT NULL,
    to_block INTEGER NOT NULL,
    payload BLOB NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    string_attributes TEXT NOT NULL DEFAULT '{}',
    numeric_attributes TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (entity_key, from_block)
);

CREATE TABLE IF NOT EXISTS last_block (
    id INTEGER NOT NULL DEFAULT 1 CHECK (id = 1),
    block INTEGER NOT NULL,
    PRIMARY KEY (id)
);
"""

INDEX_SQL = """
-- Indexes for string_attributes
CREATE INDEX IF NOT EXISTS idx_str_entity_key_value ON string_attributes (from_block, to_block, key, value);
CREATE INDEX IF NOT EXISTS idx_str_kv_temporal ON string_attributes (key, value, from_block DESC, to_block DESC);
CREATE INDEX IF NOT EXISTS idx_str_entity_key ON string_attributes (from_block, to_block, key);
CREATE INDEX IF NOT EXISTS idx_str_delete ON string_attributes (to_block);
CREATE INDEX IF NOT EXISTS idx_str_entity_kv ON string_attributes (entity_key, key, from_block DESC);

-- Indexes for numeric_attributes
CREATE INDEX IF NOT EXISTS idx_num_entity_key_value ON numeric_attributes (from_block, to_block, key, value);
CREATE INDEX IF NOT EXISTS idx_num_kv_temporal ON numeric_attributes (key, value, from_block DESC, to_block DESC);
CREATE INDEX IF NOT EXISTS idx_num_entity_key ON numeric_attributes (from_block, to_block, key);
CREATE INDEX IF NOT EXISTS idx_num_delete ON numeric_attributes (to_block);

-- Indexes for payloads
CREATE INDEX IF NOT EXISTS idx_payloads_delete ON payloads (to_block);
"""

# For backward compatibility
SCHEMA_SQL = SCHEMA_TABLES_SQL + INDEX_SQL


def drop_indexes(conn: sqlite3.Connection):
    """Drop all indexes to speed up bulk inserts."""
    print(f"Dropping indexes... - {datetime.now().strftime('%H:%M:%S')}")
    conn.execute("DROP INDEX IF EXISTS idx_str_entity_key_value")
    conn.execute("DROP INDEX IF EXISTS idx_str_kv_temporal")
    conn.execute("DROP INDEX IF EXISTS idx_str_entity_key")
    conn.execute("DROP INDEX IF EXISTS idx_str_delete")
    conn.execute("DROP INDEX IF EXISTS idx_str_entity_kv")
    conn.execute("DROP INDEX IF EXISTS idx_num_entity_key_value")
    conn.execute("DROP INDEX IF EXISTS idx_num_kv_temporal")
    conn.execute("DROP INDEX IF EXISTS idx_num_entity_key")
    conn.execute("DROP INDEX IF EXISTS idx_num_delete")
    conn.execute("DROP INDEX IF EXISTS idx_payloads_delete")
    conn.commit()
    print(f"Indexes dropped - {datetime.now().strftime('%H:%M:%S')}")


def create_indexes(conn: sqlite3.Connection):
    """Create all indexes after bulk inserts."""
    print(f"Creating indexes... - {datetime.now().strftime('%H:%M:%S')}")
    start = time.time()
    conn.executescript(INDEX_SQL)
    elapsed = time.time() - start
    print(f"Indexes created in {elapsed:.1f}s - {datetime.now().strftime('%H:%M:%S')}")


def init_database(db_path: str, input_db: str | None = None) -> sqlite3.Connection:
    """
    Initialize database, optionally copying from input database.
    
    Args:
        db_path: Path to output database
        input_db: Optional path to input database to copy from
        
    Returns:
        SQLite connection to the new/copied database
    """
    # Remove existing output file if it exists
    if os.path.exists(db_path):
        os.remove(db_path)
    
    if input_db and os.path.exists(input_db):
        # Copy input database as starting point
        print(f"Copying {input_db} to {db_path}...")
        shutil.copy2(input_db, db_path)
        conn = sqlite3.connect(db_path)
    else:
        # Create fresh database with schema (tables only, indexes later)
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA_TABLES_SQL)
    
    # Set pragmas for performance
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    
    return conn


def configure_memory(conn: sqlite3.Connection, memory_gb: int) -> None:
    """Configure SQLite memory settings for optimal bulk loading.
    
    Args:
        conn: SQLite connection
        memory_gb: Total memory to allocate in GB
    """
    # Allocate ~60% to cache, ~40% to mmap
    cache_gb = max(1, int(memory_gb * 0.6))
    mmap_gb = max(1, int(memory_gb * 0.4))
    
    # cache_size in KB (negative = KB, positive = pages)
    cache_kb = cache_gb * 1024 * 1024
    conn.execute(f"PRAGMA cache_size = -{cache_kb}")
    
    # mmap_size in bytes
    mmap_bytes = mmap_gb * 1024 * 1024 * 1024
    conn.execute(f"PRAGMA mmap_size = {mmap_bytes}")
    
    print(f"Memory config: {cache_gb}GB cache, {mmap_gb}GB mmap")


def get_max_block(conn: sqlite3.Connection) -> int:
    """Get the maximum block number from existing data."""
    cursor = conn.execute(
        "SELECT MAX(from_block) FROM string_attributes"
    )
    result = cursor.fetchone()[0]
    return result if result is not None else 0


# =============================================================================
# Top-Level Generation Functions
# =============================================================================

def generate_all_nodes(
    conn: sqlite3.Connection,
    num_datacenters: int,
    nodes_per_dc: int,
    payload_size: int,
    start_block: int,
    nodes_per_block: int,
    seed: int,
    creator_address: str = "0x0000000000000000000000000000000000dc0001",
    batch_size: int = 1000000,
) -> int:
    """
    Generate and insert all Node entities.
    
    Returns:
        Total number of nodes created
    """
    total_nodes = num_datacenters * nodes_per_dc
    print(f"Generating {total_nodes:,} nodes ({num_datacenters} DCs × {nodes_per_dc:,} nodes)...")
    
    cursor = conn.cursor()
    count = 0
    start_time = time.time()
    
    for node in generate_nodes(num_datacenters, nodes_per_dc, nodes_per_block, payload_size, start_block, seed):
        inserts = node_to_sql_inserts(node, creator_address)
        for sql, params in inserts:
            cursor.execute(sql, params)
        
        count += 1
        if count % batch_size == 0:
            conn.commit()
            elapsed = time.time() - start_time
            rate = count / elapsed
            print(f"  Nodes: {count:,}/{total_nodes:,} ({100*count/total_nodes:.1f}%) - {rate:.0f} entities/sec - {datetime.now().strftime('%H:%M:%S')}")
    
    conn.commit()
    elapsed = time.time() - start_time
    print(f"  Completed {count:,} nodes in {elapsed:.1f}s ({count/elapsed:.0f} entities/sec) - {datetime.now().strftime('%H:%M:%S')}")
    
    return count


def generate_all_workloads(
    conn: sqlite3.Connection,
    num_datacenters: int,
    nodes_per_dc: int,
    workloads_per_node: float,
    workloads_per_block: int,
    payload_size: int,
    start_block: int,
    seed: int,
    creator_address: str = "0x0000000000000000000000000000000000dc0002",
    batch_size: int = 1000,
) -> int:
    """
    Generate and insert all Workload entities.
    
    Returns:
        Total number of workloads created
    """
    workloads_per_dc = int(nodes_per_dc * workloads_per_node)
    total_workloads = num_datacenters * workloads_per_dc
    print(f"Generating {total_workloads:,} workloads ({num_datacenters} DCs × {workloads_per_dc:,} workloads)...")
    
    cursor = conn.cursor()
    count = 0
    start_time = time.time()
    
    for workload in generate_workloads(
        num_datacenters, nodes_per_dc, workloads_per_node, workloads_per_block, payload_size, start_block, seed
    ):
        inserts = workload_to_sql_inserts(workload, creator_address)
        for sql, params in inserts:
            cursor.execute(sql, params)
        
        count += 1
        if count % batch_size == 0:
            conn.commit()
            elapsed = time.time() - start_time
            rate = count / elapsed
            print(f"  Workloads: {count:,}/{total_workloads:,} ({100*count/total_workloads:.1f}%) - {rate:.0f} entities/sec - {datetime.now().strftime('%H:%M:%S')}")
    
    conn.commit()
    elapsed = time.time() - start_time
    print(f"  Completed {count:,} workloads in {elapsed:.1f}s ({count/elapsed:.0f} entities/sec) - {datetime.now().strftime('%H:%M:%S')}")
    
    return count


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate Data Center seed database for benchmark testing"
    )
    parser.add_argument(
        "--input", "-i",
        type=str,
        default=None,
        help="Input database to copy from (optional, creates empty DB if not specified)"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        required=True,
        help="Output database path"
    )
    parser.add_argument(
        "--datacenters", "-d",
        type=int,
        default=1,
        help="Number of data centers (default: 1)"
    )
    parser.add_argument(
        "--nodes-per-dc", "-n",
        type=int,
        default=100000,
        help="Number of nodes per data center (default: 100000)"
    )
    parser.add_argument(
        "--workloads-per-node", "-w",
        type=float,
        default=5.0,
        help="Workloads per node ratio, 0.2 to 10 (default: 5.0)"
    )
    parser.add_argument(
        "--payload-size", "-p",
        type=int,
        default=10000,
        help="Payload size in bytes per entity (default: 10000)"
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)"
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=1000,
        help="Commit batch size (default: 1000)"
    )
    parser.add_argument(
        "--nodes-per-block",
        type=int,
        default=DEFAULT_NODE_UPDATES_PER_BLOCK,
        help="Number of node entities created per block"
    )
    parser.add_argument(
        "--workloads-per-block",
        type=int,
        default=DEFAULT_WORKLOAD_UPDATES_PER_BLOCK,
        help="Number of workload entities created per block"
    )
    parser.add_argument(
        "--no-indexes",
        action="store_true",
        help="Drop indexes before insert and recreate after (faster for bulk loads)"
    )
    parser.add_argument(
        "--memory", "-m",
        type=int,
        default=2,
        help="Memory to use for SQLite cache+mmap in GB (default: 2)"
    )
    
    args = parser.parse_args()
    
    # Validate workloads-per-node range
    if not 0.2 <= args.workloads_per_node <= 10:
        parser.error("--workloads-per-node must be between 0.2 and 10")
    
    print("=" * 60)
    print("Data Center Seed Generator")
    print("=" * 60)
    print(f"Input:             {args.input or '(empty database)'}")
    print(f"Output:            {args.output}")
    print(f"Data centers:      {args.datacenters}")
    print(f"Nodes per DC:      {args.nodes_per_dc:,}")
    print(f"Workloads/node:    {args.workloads_per_node}")
    print(f"Payload size:      {args.payload_size:,} bytes")
    print(f"Seed:              {args.seed}")
    print()
    
    # Calculate totals
    total_nodes = args.datacenters * args.nodes_per_dc
    total_workloads = args.datacenters * int(args.nodes_per_dc * args.workloads_per_node)
    total_entities = total_nodes + total_workloads
    
    # Estimate size: ~10.5 KB per entity with 10KB payload
    est_size_gb = total_entities * (args.payload_size + 500) / (1024**3)
    
    print(f"Expected totals:")
    print(f"  Nodes:           {total_nodes:,}")
    print(f"  Workloads:       {total_workloads:,}")
    print(f"  Total entities:  {total_entities:,}")
    print(f"  Est. DB size:    ~{est_size_gb:.1f} GB")
    print()
    
    # Initialize database
    conn = init_database(args.output, args.input)
    
    # Configure memory settings
    configure_memory(conn, args.memory)
    
    # Drop indexes if --no-indexes flag is set (for faster bulk inserts)
    if args.no_indexes:
        drop_indexes(conn)
        print()
    
    # Get starting block (after existing data if any)
    start_block = get_max_block(conn) + 1
    print(f"Starting block:    {start_block}")
    print()
    
    # Generate data
    start_time = time.time()
    
    node_count = generate_all_nodes(
        conn=conn,
        num_datacenters=args.datacenters,
        nodes_per_dc=args.nodes_per_dc,
        nodes_per_block=args.nodes_per_block,
        payload_size=args.payload_size,
        start_block=start_block,
        seed=args.seed,
        batch_size=args.batch_size,
    )
    
    print()
    
    workload_count = generate_all_workloads(
        conn=conn,
        num_datacenters=args.datacenters,
        nodes_per_dc=args.nodes_per_dc,
        workloads_per_node=args.workloads_per_node,
        workloads_per_block=args.workloads_per_block,
        payload_size=args.payload_size,
        start_block=start_block,
        seed=args.seed,
        batch_size=args.batch_size,
    )
    
    # Update last_block
    conn.execute(
        "INSERT OR REPLACE INTO last_block (id, block) VALUES (1, ?)",
        (start_block,)
    )
    conn.commit()
    
    # Recreate indexes if --no-indexes flag was set
    if args.no_indexes:
        print()
        create_indexes(conn)
    
    total_time = time.time() - start_time
    
    # Get final database size
    conn.close()
    db_size = os.path.getsize(args.output)
    
    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Nodes created:     {node_count:,}")
    print(f"Workloads created: {workload_count:,}")
    print(f"Total entities:    {node_count + workload_count:,}")
    print(f"Total time:        {total_time:.1f}s")
    print(f"Rate:              {(node_count + workload_count) / total_time:.0f} entities/sec")
    print(f"Database size:     {db_size / (1024**3):.2f} GB")
    print(f"Output:            {args.output}")


if __name__ == "__main__":
    main()
