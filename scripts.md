# Data Center Database Scripts

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

## Script 3: `generate_dc_load.py` — Load Generator (TODO)

Generates realistic churn: status changes, new workloads, completions.

---

## Manual Database Inspection

Use SQLite CLI to manually inspect entity data.

### Open Database

```bash
sqlite3 data/dc_test.db
```

Show colum names as 1st row
```sql
.headers on
```

Other useful modes:
```sql
-- bordered table
.mode table 

-- Unicode box drawing
.mode box

-- one value per line (good for wide rows)
.mode line
```

### Find Entity Keys

```sql
-- List some entity keys (as hex)
SELECT hex(entity_key) FROM payloads LIMIT 5;

-- Find entity key by node_id
SELECT hex(entity_key) FROM string_attributes 
WHERE key = 'node_id' AND value = 'node_01_000001';

-- Find entity key by workload_id
SELECT hex(entity_key) FROM string_attributes 
WHERE key = 'workload_id' AND value = 'wl_01_000042';
```

### Inspect Entity by Key

Replace `<HEX_KEY>` with the entity key (e.g., `3938da435c3a374f356c3178e6cede3e143df06959d07d92ce3c2c506ce89f41`).

#### Payload (all attributes except payload content)

```sql
SELECT 
  hex(entity_key) as entity_key,
  from_block,
  to_block,
  LENGTH(payload) as payload_size,
  content_type,
  string_attributes,
  numeric_attributes
FROM payloads 
WHERE entity_key = X'<HEX_KEY>';
```

#### String Attributes

```sql
SELECT key, value, from_block, to_block
FROM string_attributes 
WHERE entity_key = X'<HEX_KEY>'
ORDER BY key;
```

#### Numeric Attributes

```sql
SELECT key, value, from_block, to_block
FROM numeric_attributes 
WHERE entity_key = X'<HEX_KEY>'
ORDER BY key;
```

### Example: Full Entity Inspection

```sql
-- 1. Find a node entity key
SELECT hex(entity_key) as key FROM string_attributes 
WHERE key = 'node_id' AND value = 'node_01_000001';

-- 2. Use the key to inspect (example key shown)
-- Payload info
SELECT hex(entity_key), from_block, to_block, LENGTH(payload) as payload_size
FROM payloads WHERE entity_key = X'...';

-- String attributes
SELECT key, value FROM string_attributes 
WHERE entity_key = X'...' ORDER BY key;

-- Numeric attributes  
SELECT key, value FROM numeric_attributes
WHERE entity_key = X'...' ORDER BY key;
```

### Useful Queries

```sql
-- Count entities by type
SELECT value as type, COUNT(DISTINCT entity_key) as count
FROM string_attributes WHERE key = 'type' GROUP BY value;

-- Check TTL distribution (to_block - from_block)
SELECT 
  CASE 
    WHEN (to_block - from_block) < 10800 THEN 'short (1-6h)'
    WHEN (to_block - from_block) < 302400 THEN 'medium (12h-7d)'
    ELSE 'long (7-28d)'
  END as ttl_category,
  COUNT(*) as count
FROM payloads
WHERE to_block < 9223372036854775807
GROUP BY ttl_category;

-- Check block spread
SELECT MIN(from_block), MAX(from_block), COUNT(DISTINCT from_block) as blocks
FROM payloads;
```
