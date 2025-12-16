"""
Append Data Center data block-by-block for realistic chain progression.

Creates Node and Workload entities in a block-by-block pattern where each block
contains nodes and their associated workloads together.

Block composition (for nodes-per-block=2, workloads-per-node=5):
  - Node A + 5 workloads for Node A
  - Node B + 5 workloads for Node B
  - Total: 2 nodes + 10 workloads = 12 entities per block

Usage:
    # Append 1000 blocks to existing database
    uv run python -m src.db.append_dc_data \
        --input data/dc_seed.db \
        --blocks 1000 \
        --nodes-per-block 2 \
        --workloads-per-node 5 \
        --output data/dc_extended.db

    # Create new database with block-by-block data
    uv run python -m src.db.append_dc_data \
        --blocks 500 \
        --nodes-per-block 3 \
        --workloads-per-node 4 \
        --percentage-assigned 0.8 \
        --output data/dc_blocks.db
"""

import argparse
import os
import random
import secrets
import shutil
import sqlite3
import time
import uuid
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


def make_node_id(dc_num: int, node_num: int, seed: int) -> str:
    """Generate node ID using deterministic UUID to avoid collisions."""
    # Create deterministic UUID from seed, dc_num, and node_num
    rng = random.Random(f"{seed}:node:{dc_num}:{node_num}")
    uuid_bytes = bytes(rng.getrandbits(8) for _ in range(16))
    node_uuid = uuid.UUID(bytes=uuid_bytes)
    return f"node_{node_uuid.hex[:12]}"


def make_workload_id(dc_num: int, workload_num: int, seed: int) -> str:
    """Generate workload ID using deterministic UUID to avoid collisions."""
    # Create deterministic UUID from seed, dc_num, and workload_num
    rng = random.Random(f"{seed}:workload:{dc_num}:{workload_num}")
    uuid_bytes = bytes(rng.getrandbits(8) for _ in range(16))
    workload_uuid = uuid.UUID(bytes=uuid_bytes)
    return f"wl_{workload_uuid.hex[:12]}"


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
    status: str | None = None,
) -> NodeEntity:
    """Create a single Node entity with randomized attributes.
    
    Args:
        status: If provided, use this status instead of sampling from distribution.
    """
    rng = random.Random(f"{seed}:node:{dc_num}:{node_num}")
    
    dc_id = make_dc_id(dc_num)
    node_id = make_node_id(dc_num, node_num, seed)
    entity_key = make_entity_key(node_id, seed)
    
    # Sample attributes from distributions
    region = sample_from_distribution(rng, get_region_distribution())
    if status is None:
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
    status: str | None = None,
    assigned_node: str | None = None,
) -> WorkloadEntity:
    """Create a single Workload entity with randomized attributes.
    
    Args:
        status: If provided, use this status instead of sampling from distribution.
        assigned_node: If provided, use this as the assigned node ID.
    """
    rng = random.Random(f"{seed}:workload:{dc_num}:{workload_num}")
    
    dc_id = make_dc_id(dc_num)
    workload_id = make_workload_id(dc_num, workload_num, seed)
    entity_key = make_entity_key(workload_id, seed)
    
    # Sample attributes from distributions
    if status is None:
        status = sample_from_distribution(rng, get_workload_status_distribution())
    region = sample_from_distribution(rng, get_region_distribution())
    vm_type = sample_from_distribution(rng, get_vm_type_distribution())
    req_cpu = sample_from_distribution(rng, get_req_cpu_distribution())
    req_ram = sample_from_distribution(rng, get_req_ram_distribution())
    max_hours = sample_from_distribution(rng, get_max_hours_distribution())
    ttl_blocks = sample_ttl_blocks(rng)

    # Use provided assigned_node or determine based on status
    if assigned_node is None:
        if status == "running":
            node_num = workload_to_node_num(workload_num, nodes_per_dc)
            assigned_node = make_node_id(dc_num, node_num, seed)
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
# Block-by-Block Entity Generation
# =============================================================================

@dataclass
class BlockData:
    """Data for a single block containing nodes and their workloads."""
    block_num: int
    nodes: list[NodeEntity]
    workloads: list[WorkloadEntity]


def generate_blocks(
    num_blocks: int,
    nodes_per_block: int,
    workloads_per_node: int,
    percentage_assigned: float,
    payload_size: int,
    start_block: int,
    seed: int,
    dc_num: int = 1,
) -> Iterator[BlockData]:
    """
    Generate blocks with nodes and their associated workloads.
    
    Each block contains:
    - N nodes (nodes_per_block)
    - For each node: M workloads (workloads_per_node)
    
    Args:
        num_blocks: Number of blocks to generate
        nodes_per_block: Number of nodes per block
        workloads_per_node: Number of workloads per node
        percentage_assigned: Fraction of nodes that are busy (0.0-1.0)
        payload_size: Size of payload in bytes
        start_block: Starting block number
        seed: Random seed
        dc_num: Data center number (default: 1)
    """
    rng = random.Random(f"{seed}:blocks")
    
    # Global counters for unique IDs
    node_counter = 0
    workload_counter = 0
    
    for block_idx in range(num_blocks):
        current_block = start_block + block_idx
        nodes = []
        workloads = []
        
        for _ in range(nodes_per_block):
            node_counter += 1
            
            # Determine if this node is busy (has assigned workload)
            is_busy = rng.random() < percentage_assigned
            node_status = "busy" if is_busy else "available"
            
            # Create the node
            node = create_node(
                dc_num=dc_num,
                node_num=node_counter,
                payload_size=payload_size,
                block=current_block,
                seed=seed,
                status=node_status,
            )
            nodes.append(node)
            
            # Create workloads for this node
            for wl_idx in range(workloads_per_node):
                workload_counter += 1
                
                # First workload is assigned if node is busy
                if is_busy and wl_idx == 0:
                    wl_status = "running"
                    wl_assigned = node.node_id
                else:
                    wl_status = "pending"
                    wl_assigned = ""
                
                workload = create_workload(
                    dc_num=dc_num,
                    workload_num=workload_counter,
                    nodes_per_dc=node_counter,  # Not used when assigned_node provided
                    payload_size=payload_size,
                    block=current_block,
                    seed=seed,
                    status=wl_status,
                    assigned_node=wl_assigned,
                )
                workloads.append(workload)
        
        yield BlockData(
            block_num=current_block,
            nodes=nodes,
            workloads=workloads,
        )


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

def append_blocks(
    conn: sqlite3.Connection,
    num_blocks: int,
    nodes_per_block: int,
    workloads_per_node: int,
    percentage_assigned: float,
    payload_size: int,
    start_block: int,
    seed: int,
    creator_address: str = "0x0000000000000000000000000000000000dc0001",
    batch_size: int = 1,
) -> tuple[int, int, int]:
    """
    Generate and insert blocks with nodes and workloads together.
    
    Args:
        conn: SQLite connection
        num_blocks: Number of blocks to generate
        nodes_per_block: Nodes per block
        workloads_per_node: Workloads per node
        percentage_assigned: Fraction of nodes that are busy (0.0-1.0)
        payload_size: Payload size in bytes
        start_block: Starting block number
        seed: Random seed
        creator_address: Creator address for entities
        batch_size: Commit every N blocks (default: 1 = commit per block)
    
    Returns:
        Tuple of (node_count, workload_count, final_block)
    """
    entities_per_block = nodes_per_block + (nodes_per_block * workloads_per_node)
    total_entities = num_blocks * entities_per_block
    
    print(f"Generating {num_blocks:,} blocks...")
    print(f"  Nodes per block:      {nodes_per_block}")
    print(f"  Workloads per node:   {workloads_per_node}")
    print(f"  Entities per block:   {entities_per_block}")
    print(f"  Total entities:       {total_entities:,}")
    print(f"  Percentage assigned:  {percentage_assigned*100:.0f}%")
    print()
    
    cursor = conn.cursor()
    node_count = 0
    workload_count = 0
    block_count = 0
    final_block = start_block
    start_time = time.time()
    
    for block_data in generate_blocks(
        num_blocks=num_blocks,
        nodes_per_block=nodes_per_block,
        workloads_per_node=workloads_per_node,
        percentage_assigned=percentage_assigned,
        payload_size=payload_size,
        start_block=start_block,
        seed=seed,
    ):
        # Insert all nodes in this block
        for node in block_data.nodes:
            inserts = node_to_sql_inserts(node, creator_address)
            for sql, params in inserts:
                cursor.execute(sql, params)
            node_count += 1
        
        # Insert all workloads in this block
        for workload in block_data.workloads:
            inserts = workload_to_sql_inserts(workload, creator_address)
            for sql, params in inserts:
                cursor.execute(sql, params)
            workload_count += 1
        
        block_count += 1
        final_block = block_data.block_num
        
        # Commit every batch_size blocks
        if block_count % batch_size == 0:
            conn.commit()
        
        # Progress every 100 blocks or 1000 entities
        if block_count % 100 == 0 or (node_count + workload_count) % 1000 == 0:
            elapsed = time.time() - start_time
            rate = (node_count + workload_count) / elapsed if elapsed > 0 else 0
            print(f"  Block {block_count:,}/{num_blocks:,} ({100*block_count/num_blocks:.1f}%) - "
                  f"{node_count + workload_count:,} entities - {rate:.0f}/sec - "
                  f"{datetime.now().strftime('%H:%M:%S')}")
    
    conn.commit()
    elapsed = time.time() - start_time
    rate = (node_count + workload_count) / elapsed if elapsed > 0 else 0
    print(f"  Completed {block_count:,} blocks in {elapsed:.1f}s ({rate:.0f} entities/sec) - "
          f"{datetime.now().strftime('%H:%M:%S')}")
    
    return node_count, workload_count, final_block


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Append Data Center data block-by-block for realistic chain progression"
    )
    parser.add_argument(
        "--input", "-i",
        type=str,
        default=None,
        help="Input database to append to (optional, creates empty DB if not specified)"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        required=True,
        help="Output database path"
    )
    parser.add_argument(
        "--blocks", "-b",
        type=int,
        default=100,
        help="Number of blocks to generate (default: 100)"
    )
    parser.add_argument(
        "--nodes-per-block",
        type=int,
        default=DEFAULT_NODE_UPDATES_PER_BLOCK,
        help=f"Number of nodes per block (default: {DEFAULT_NODE_UPDATES_PER_BLOCK})"
    )
    parser.add_argument(
        "--workloads-per-node", "-w",
        type=int,
        default=5,
        help="Number of workloads per node (default: 5)"
    )
    parser.add_argument(
        "--percentage-assigned", "-a",
        type=float,
        default=0.8,
        help="Fraction of nodes that are busy with one assigned workload (0.0-1.0, default: 0.8)"
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
        default=None,
        help="Random seed for reproducibility (default: random)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1,
        help="Commit every N blocks (default: 1 = commit per block)"
    )
    parser.add_argument(
        "--memory", "-m",
        type=int,
        default=2,
        help="Memory to use for SQLite cache+mmap in GB (default: 2)"
    )
    
    args = parser.parse_args()
    
    # Validate percentage-assigned range
    if not 0.0 <= args.percentage_assigned <= 1.0:
        parser.error("--percentage-assigned must be between 0.0 and 1.0")
    
    # Generate random seed if not provided
    if args.seed is None:
        args.seed = random.randint(1, 2**31 - 1)
    
    # Calculate derived values
    entities_per_block = args.nodes_per_block + (args.nodes_per_block * args.workloads_per_node)
    total_nodes = args.blocks * args.nodes_per_block
    total_workloads = args.blocks * args.nodes_per_block * args.workloads_per_node
    total_entities = total_nodes + total_workloads
    
    # Estimate size: ~10.5 KB per entity with 10KB payload
    est_size_gb = total_entities * (args.payload_size + 500) / (1024**3)
    
    print("=" * 60)
    print("Data Center Block Appender")
    print("=" * 60)
    print(f"Input:              {args.input or '(empty database)'}")
    print(f"Output:             {args.output}")
    print(f"Blocks:             {args.blocks:,}")
    print(f"Nodes per block:    {args.nodes_per_block}")
    print(f"Workloads per node: {args.workloads_per_node}")
    print(f"Entities per block: {entities_per_block}")
    print(f"% assigned:         {args.percentage_assigned*100:.0f}%")
    print(f"Payload size:       {args.payload_size:,} bytes")
    print(f"Seed:               {args.seed}")
    print()
    
    print(f"Expected totals:")
    print(f"  Nodes:            {total_nodes:,}")
    print(f"  Workloads:        {total_workloads:,}")
    print(f"  Total entities:   {total_entities:,}")
    print(f"  Est. added size:  ~{est_size_gb:.1f} GB")
    print()
    
    # Initialize database
    conn = init_database(args.output, args.input)
    
    # Configure memory settings
    configure_memory(conn, args.memory)
    
    # Get starting block (after existing data if any)
    start_block = get_max_block(conn) + 1
    print(f"Starting block:     {start_block}")
    print()
    
    # Generate data
    start_time = time.time()
    
    node_count, workload_count, final_block = append_blocks(
        conn=conn,
        num_blocks=args.blocks,
        nodes_per_block=args.nodes_per_block,
        workloads_per_node=args.workloads_per_node,
        percentage_assigned=args.percentage_assigned,
        payload_size=args.payload_size,
        start_block=start_block,
        seed=args.seed,
        batch_size=args.batch_size,
    )
    
    # Update last_block
    conn.execute(
        "INSERT OR REPLACE INTO last_block (id, block) VALUES (1, ?)",
        (final_block,)
    )
    conn.commit()
    
    total_time = time.time() - start_time
    
    # Get final database size
    conn.close()
    db_size = os.path.getsize(args.output)
    
    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Blocks created:    {args.blocks:,}")
    print(f"Block range:       {start_block:,} - {final_block:,}")
    print(f"Nodes created:     {node_count:,}")
    print(f"Workloads created: {workload_count:,}")
    print(f"Total entities:    {node_count + workload_count:,}")
    print(f"Total time:        {total_time:.1f}s")
    print(f"Rate:              {(node_count + workload_count) / total_time:.0f} entities/sec")
    print(f"Database size:     {db_size / (1024**3):.2f} GB")
    print(f"Output:            {args.output}")
    print(f"Seed:              {args.seed}")


if __name__ == "__main__":
    main()
