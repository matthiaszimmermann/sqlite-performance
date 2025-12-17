# Data Center Database Scripts

## Table of Contents

1. [Script 1: `generate_dc_seed.py` — Seed Database Generator](#script-1-generate_dc_seedpy--seed-database-generator)
2. [Script 2: `inspect_dc_db.py` — Database Inspector](#script-2-inspect_dc_dbpy--database-inspector)
3. [Script 3: `append_dc_data.py` — Block-by-Block Data Appender](#script-3-append_dc_datapy--block-by-block-data-appender)
4. [Script 4: `query_dc_benchmark.py` — Query Performance Benchmark](#script-4-query_dc_benchmarkpy--query-performance-benchmark)

---

This document describes the scripts for generating and inspecting Data Center benchmark databases.

## Script 1: `generate_dc_seed.py` — Seed Database Generator

Creates the initial state ("day 0") snapshot with all nodes and workloads.

### Usage

```bash
# Small test run (300 entities, ~2 MB)
uv run python -m src.db.generate_dc_seed \
  --datacenters 1 \
  --nodes-per-dc 100 \
  --workloads-per-node 2 \
  --payload-size 1000 \
  --output data/dc_test.db

# 2x mendoza scale (~27 GB)
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
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--input, -i` | (empty DB) | Input database to copy from (optional) |
| `--output, -o` | required | Output database path |
| `--datacenters, -d` | 1 | Number of data centers to generate |
| `--nodes-per-dc, -n` | 100000 | Number of nodes per data center |
| `--workloads-per-node, -w` | 5.0 | Workloads per node ratio (0.2–10) |
| `--payload-size, -p` | 10000 | Payload size in bytes per entity |
| `--nodes-per-block` | 60 | Node entities created per block |
| `--workloads-per-block` | 600 | Workload entities created per block |
| `--seed, -s` | 42 | Random seed for reproducibility |
| `--batch-size, -b` | 1000 | Commit batch size |

### Distributions

| Attribute | Distribution |
|-----------|--------------|
| `region` | 40% eu-west, 35% us-east, 25% asia-pac |
| `vm_type` | 70% cpu, 25% gpu, 5% gpu_large |
| `status` (node) | 70% available, 25% busy, 5% offline |
| `status` (workload) | 15% pending, 80% running, 5% completed |
| `ttl` | 10% 1-6h, 60% 12h-7d, 30% 7-28d |

---

## Script 2: `inspect_dc_db.py` — Database Inspector

Reports statistics and sample entities from a generated database.

### Usage

```bash
# Text output
uv run python -m src.db.inspect_dc_db data/dc_test.db

# JSON output
uv run python -m src.db.inspect_dc_db data/dc_test.db --json
```

### Output

- File size
- Entity counts (nodes, workloads, per DC)
- Attribute statistics (avg per entity)
- TTL statistics (min/max for nodes and workloads)
- Row counts per table
- Random example node and workload

---

## Script 3: `append_dc_data.py` — Block-by-Block Data Appender

Appends data to an existing database block-by-block, simulating realistic chain progression where each block contains nodes and their associated workloads together.

### Usage

```bash
# Create new database with 100 blocks
uv run python -m src.db.append_dc_data \
  --blocks 100 \
  --nodes-per-block 10 \
  --workloads-per-node 3 \
  --percentage-assigned 0.5 \
  --payload-size 1000 \
  --output data/dc_blocks.db

# Append 50 more blocks to existing database
uv run python -m src.db.append_dc_data \
  --input data/dc_blocks.db \
  --blocks 50 \
  --nodes-per-block 10 \
  --workloads-per-node 3 \
  --percentage-assigned 0.8 \
  --output data/dc_blocks_extended.db
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--input, -i` | (empty DB) | Input database to append to (optional) |
| `--output, -o` | required | Output database path |
| `--blocks, -b` | 100 | Number of blocks to generate |
| `--nodes-per-block, -n` | 10 | Number of nodes created per block |
| `--workloads-per-node, -w` | 3 | Workloads per node |
| `--percentage-assigned` | 0.5 | Fraction of nodes marked "busy" with one assigned workload (0.0–1.0) |
| `--payload-size, -p` | 10000 | Payload size in bytes per entity |
| `--seed, -s` | random | Random seed (random if not provided) |
| `--batch-size` | 1000 | Commit batch size |
| `--memory, -m` | 2 | Memory allocation in GB for SQLite cache |

### Block Composition

Each block contains:
- N nodes (controlled by `--nodes-per-block`)
- N × W workloads (where W = `--workloads-per-node`)

For assigned nodes (controlled by `--percentage-assigned`):
- Node status is set to "busy"
- First workload of that node has status "running" and `assigned_node` set to the node ID

### Key Differences from `generate_dc_seed.py`

| Feature | `generate_dc_seed.py` | `append_dc_data.py` |
|---------|----------------------|---------------------|
| **Data generation** | Bulk (all nodes, then all workloads) | Block-by-block (nodes + workloads together) |
| **Use case** | Initial "day 0" snapshot | Simulating chain progression |
| **Entity IDs** | Sequential (`node_01_000001`) | UUID-based (`node_f1a57af1645c`) |
| **Assignment control** | Random distribution | Explicit `--percentage-assigned` |

---

## Script 4: `query_dc_benchmark.py` — Query Performance Benchmark

Measures read query performance with a configurable mix of query types against a DC database.

### Usage

```bash
# Run benchmark with default mix (1000 queries)
uv run python -m src.db.query_dc_benchmark \
  --database data/dc_seed_2x.db \
  --queries 1000

# Specify current block (for bi-temporal queries)
uv run python -m src.db.query_dc_benchmark \
  --database data/dc_seed_2x.db \
  --current-block 500 \
  --queries 5000

# With CSV logging for Jupyter analysis
uv run python -m src.db.query_dc_benchmark \
  --database data/dc_seed_2x.db \
  --queries 5000 \
  --log data/benchmark.log

# Custom query mix weights
uv run python -m src.db.query_dc_benchmark \
  --database data/dc_seed_2x.db \
  --queries 10000 \
  --mix '{"point_by_id": 0.3, "point_by_key": 0.2, "point_miss": 0.1, "node_filter": 0.2, "workload_simple": 0.1, "workload_specific": 0.1}'
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--database, -d` | required | Path to database file |
| `--queries, -q` | 1000 | Total number of queries to execute |
| `--current-block` | from DB | Block number for bi-temporal queries (default: `last_block` from DB) |
| `--mix` | default weights | JSON object with query type weights |
| `--warmup` | 100 | Number of warmup queries before measurement |
| `--memory, -m` | 16 | Memory allocation in GB for SQLite |
| `--seed, -s` | random | Random seed for reproducibility |
| `--log, -l` | none | Path to CSV log file for per-query details |

### Query Types

| Query Type | Key | Description |
|------------|-----|-------------|
| **Point by ID (hit)** | `point_by_id` | Lookup entity by `node_id` or `workload_id` (requires join) |
| **Point by Key (hit)** | `point_by_key` | Direct lookup by `entity_key` (fastest) |
| **Point Miss** | `point_miss` | Lookup non-existent UUID (guaranteed 0 results) |
| **Node Filter** | `node_filter` | Find available nodes matching: region, vm_type, min_ram, min_cpu, min_hours, max_price |
| **Workload Simple** | `workload_simple` | Find pending workloads (status filter only) |
| **Workload Specific** | `workload_specific` | Find pending workloads matching: region, vm_type |

### Default Query Mix

```python
QUERY_MIX = {
    "point_by_id": 0.20,       # 20% - Point lookup by node_id/workload_id
    "point_by_key": 0.15,      # 15% - Direct entity_key lookup
    "point_miss": 0.10,        # 10% - Non-existent entity lookup
    "node_filter": 0.25,       # 25% - Filter available nodes
    "workload_simple": 0.15,   # 15% - Find pending workloads
    "workload_specific": 0.15, # 15% - Find pending workloads with filters
}
```

### Architecture

```
query_dc_benchmark.py
│
├── Constants
│   ├── QUERY_MIX: dict[str, float]     # Default query type weights
│   └── QueryType (Enum)                 # Query type identifiers
│
├── Data Classes
│   ├── QueryParams                      # Parameters for a single query
│   │   ├── current_block: int
│   │   ├── entity_id: str | None        # node_id or workload_id
│   │   ├── entity_key: bytes | None     # Direct key lookup
│   │   ├── region: str | None
│   │   ├── vm_type: str | None
│   │   ├── min_cpu: int | None
│   │   ├── min_ram: int | None
│   │   ├── min_hours: int | None
│   │   └── max_price: int | None
│   │
│   └── QueryResult                      # Result of a single query
│       ├── query_type: QueryType
│       ├── latency_ms: float
│       ├── row_count: int
│       ├── success: bool
│       └── error: str | None
│
├── QueryGenerator
│   ├── __init__(conn, current_block, seed)
│   ├── _load_sample_ids()              # Pre-load valid IDs for point queries
│   ├── _load_sample_keys()             # Pre-load valid entity_keys
│   ├── generate_params(query_type) -> QueryParams
│   └── generate_random_uuid() -> str   # For miss queries
│
├── QueryExecutor
│   ├── __init__(conn, current_block, log_file)
│   ├── execute(query_type, params) -> QueryResult
│   ├── _log_query(query_type, result, params)  # CSV logging
│   ├── _execute_point_by_id(params) -> QueryResult
│   ├── _execute_point_by_key(params) -> QueryResult
│   ├── _execute_point_miss(params) -> QueryResult
│   ├── _execute_node_filter(params) -> QueryResult
│   ├── _execute_workload_simple(params) -> QueryResult
│   └── _execute_workload_specific(params) -> QueryResult
│
├── BenchmarkRunner
│   ├── __init__(conn, generator, executor, query_mix)
│   ├── run(num_queries, warmup) -> list[QueryResult]
│   ├── _select_query_type() -> QueryType  # Weighted random selection
│   └── compute_statistics(results) -> dict
│
├── Reporter
│   └── print_report(stats, config)
│
└── main()
    ├── Parse CLI arguments
    ├── Connect to database
    ├── Configure memory settings
    ├── Get current_block (from args or DB)
    ├── Open log file (if --log specified)
    ├── Initialize QueryGenerator
    ├── Initialize QueryExecutor (with log file)
    ├── Initialize BenchmarkRunner
    ├── Run warmup queries
    ├── Run benchmark queries
    ├── Compute statistics
    ├── Print report
    └── Close log file
```

### Bi-Temporal Query Pattern

All queries use the bi-temporal pattern to get the latest valid state:

```sql
WHERE from_block <= :current_block 
  AND to_block > :current_block
ORDER BY from_block DESC  -- Get latest version if multiple exist
```

### Output

```
============================================================
Query Benchmark Results
============================================================
Database:           data/dc_seed_2x.db
Current block:      1000
Total queries:      10000
Successful:         10000
Failed:             0
Warmup queries:     100

--- Latency (ms) ---
Query Type              Count      p50      p95      p99      max
------------------------------------------------------------
point_by_id              2000     0.12     0.25     0.45     1.23
point_by_key             1500     0.05     0.12     0.22     0.89
point_miss               1000     0.08     0.18     0.35     0.95
node_filter              2500     2.34     5.67    12.45    45.67
workload_simple          1500     1.23     3.45     8.90    25.34
workload_specific        1500     1.89     4.56    10.23    38.90
------------------------------------------------------------
OVERALL                 10000     0.85     4.12     9.56    45.67

--- Throughput ---
Total query time:   12.34s
Queries/sec:        810.4
Avg latency:        1.23ms
Avg result set:     18.5 rows

--- Query Distribution ---
point_by_id              2000 ( 20.0%)
point_by_key             1500 ( 15.0%)
point_miss               1000 ( 10.0%)
node_filter              2500 ( 25.0%)
workload_simple          1500 ( 15.0%)
workload_specific        1500 ( 15.0%)
============================================================

Total benchmark time: 12.5s
Query log written to: data/benchmark.log
```

### CSV Log Format

When `--log` is specified, each query is logged to a CSV file:

```csv
timestamp,query_type,latency_ms,row_count,params
2025-12-16T14:42:12.414788,node_filter,7.090,0,"{"current_block": 1, "region": "eu-west", ...}"
2025-12-16T14:42:12.426748,workload_simple,11.749,94,"{"current_block": 1}"
2025-12-16T14:42:12.427451,point_by_id,0.487,19,"{"current_block": 1, "entity_id": "wl_01_000598"}"
```

Load in Jupyter/pandas:
```python
import pandas as pd
import json

df = pd.read_csv("data/benchmark.log")
df["params"] = df["params"].apply(json.loads)  # Parse JSON params
df["timestamp"] = pd.to_datetime(df["timestamp"])

# Analyze by query type
df.groupby("query_type")["latency_ms"].describe()
```

### SQL Query Templates

#### Point by ID (hit)
```sql
-- Step 1: Get entity_key from ID
SELECT entity_key FROM string_attributes 
WHERE key = 'node_id' AND value = :node_id
  AND from_block <= :current_block AND to_block > :current_block
ORDER BY from_block DESC LIMIT 1;

-- Step 2: Get all attributes for entity
SELECT key, value FROM string_attributes 
WHERE entity_key = :entity_key
  AND from_block <= :current_block AND to_block > :current_block;
SELECT key, value FROM numeric_attributes 
WHERE entity_key = :entity_key
  AND from_block <= :current_block AND to_block > :current_block;
SELECT payload FROM payloads 
WHERE entity_key = :entity_key
  AND from_block <= :current_block AND to_block > :current_block;
```

#### Point by Key (hit)
```sql
-- Direct lookup (fastest path)
SELECT key, value FROM string_attributes 
WHERE entity_key = :entity_key
  AND from_block <= :current_block AND to_block > :current_block;
SELECT key, value FROM numeric_attributes 
WHERE entity_key = :entity_key
  AND from_block <= :current_block AND to_block > :current_block;
SELECT payload FROM payloads 
WHERE entity_key = :entity_key
  AND from_block <= :current_block AND to_block > :current_block;
```

#### Node Filter
```sql
SELECT DISTINCT sa_status.entity_key
FROM string_attributes sa_status
JOIN string_attributes sa_region ON sa_status.entity_key = sa_region.entity_key
JOIN string_attributes sa_vm ON sa_status.entity_key = sa_vm.entity_key
JOIN numeric_attributes na_cpu ON sa_status.entity_key = na_cpu.entity_key
JOIN numeric_attributes na_ram ON sa_status.entity_key = na_ram.entity_key
JOIN numeric_attributes na_hours ON sa_status.entity_key = na_hours.entity_key
JOIN numeric_attributes na_price ON sa_status.entity_key = na_price.entity_key
WHERE sa_status.key = 'status' AND sa_status.value = 'available'
  AND sa_region.key = 'region' AND sa_region.value = :region
  AND sa_vm.key = 'vm_type' AND sa_vm.value = :vm_type
  AND na_cpu.key = 'cpu_count' AND na_cpu.value >= :min_cpu
  AND na_ram.key = 'ram_gb' AND na_ram.value >= :min_ram
  AND na_hours.key = 'avail_hours' AND na_hours.value >= :min_hours
  AND na_price.key = 'price_hour' AND na_price.value <= :max_price
  AND sa_status.from_block <= :current_block AND sa_status.to_block > :current_block
  -- (temporal filters on all joins)
ORDER BY na_price.value ASC
LIMIT 10;
```

#### Workload Simple
```sql
SELECT DISTINCT sa.entity_key
FROM string_attributes sa
WHERE sa.key = 'status' AND sa.value = 'pending'
  AND sa.from_block <= :current_block AND sa.to_block > :current_block
  AND EXISTS (
    SELECT 1 FROM string_attributes sa2 
    WHERE sa2.entity_key = sa.entity_key 
      AND sa2.key = 'type' AND sa2.value = 'workload'
      AND sa2.from_block <= :current_block AND sa2.to_block > :current_block
  )
LIMIT 100;
```

#### Workload Specific
```sql
SELECT DISTINCT sa_status.entity_key
FROM string_attributes sa_status
JOIN string_attributes sa_region ON sa_status.entity_key = sa_region.entity_key
JOIN string_attributes sa_vm ON sa_status.entity_key = sa_vm.entity_key
WHERE sa_status.key = 'status' AND sa_status.value = 'pending'
  AND sa_region.key = 'region' AND sa_region.value = :region
  AND sa_vm.key = 'vm_type' AND sa_vm.value = :vm_type
  AND sa_status.from_block <= :current_block AND sa_status.to_block > :current_block
  AND sa_region.from_block <= :current_block AND sa_region.to_block > :current_block
  AND sa_vm.from_block <= :current_block AND sa_vm.to_block > :current_block
  AND EXISTS (
    SELECT 1 FROM string_attributes sa2 
    WHERE sa2.entity_key = sa_status.entity_key 
      AND sa2.key = 'type' AND sa2.value = 'workload'
      AND sa2.from_block <= :current_block AND sa2.to_block > :current_block
  )
LIMIT 100;
```
