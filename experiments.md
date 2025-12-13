# SQLite Index Performance Experiments

This document captures benchmark results comparing insert performance across different index configurations.

## Executive Summary

### Key Conclusions

1. **Payload size dominates performance** — Large payloads (50KB) cause a **22x slowdown** compared to tiny payloads. This is the biggest factor for arkiv performance.

2. **Index count barely matters with realistic payloads** — Removing 7 indexes (13→6) made **<3% difference** when payloads are involved. Keep bi-temporality!

3. **Index overhead only matters for tiny rows** — The 2.6x slowdown we measured in early experiments disappears when realistic payload sizes are used.
5. **Commit frequency is critical** — Smaller batches (500 vs 10,000 rows per commit) dramatically increase absolute overhead.

### Arkiv-Specific Findings (Full Schema, Realistic Payloads)

| Entity Type | Payload | Entities/sec | Rows/sec |
|-------------|---------|--------------|----------|
| Typical (5 str + 3 int) | 5 KB | ~700 | ~6,400 |
| Large (10 str + 5 int) | 50 KB | ~85 | ~1,400 |
| Minimal (2 str + 1 int) | 1 KB | ~3,100 | ~12,500 |

### Recommendations

1. **Optimize payload handling first** — For large entities, consider external storage, compression, or reducing payload frequency.

2. **Review index necessity** — Each of the 5 secondary indexes in the bi-temporal schema has a cost. Are all query patterns actually used?

3. **Consider deferred indexing** — For bulk imports, create indexes after data load.

4. **Batch size is fine** — Arkiv's ~500 attributes/block commit pattern is reasonable; larger batches would help but aren't critical.

### Experiments Needed to Validate

| Question | Suggested Experiment |
|----------|---------------------|
| Which specific index is most expensive? | Benchmark with each index added incrementally (0→1→2→3→4→5→6) |
| Does Go show same results? | Port benchmark to Go with same schema |
| Real arkiv data distribution? | Use actual arkiv entity/attribute patterns, not synthetic |
| Read performance tradeoff? | Benchmark query performance with fewer indexes |
| SSD vs HDD difference? | Run on different storage types |

---

## Test Configurations

### Schema Variants

| Configuration | Indexes | Description |
|--------------|---------|-------------|
| No indexes | 0 | Reference baseline, no primary key or indexes |
| Simple EVA | 2 | Primary key + 1 lookup index (typical EVA pattern) |
| Bi-temporal EVA | 6 | Primary key + 5 indexes (arkiv-style temporal schema) |

### Bi-temporal Index Details

The bi-temporal schema includes these indexes (matching the arkiv schema):

```sql
PRIMARY KEY (entity_key, key, from_block)
CREATE INDEX idx_entity_key_value ON test_attributes (from_block, to_block, key, value)
CREATE INDEX idx_kv_temporal ON test_attributes (key, value, from_block DESC, to_block DESC)
CREATE INDEX idx_entity_key ON test_attributes (from_block, to_block, key)
CREATE INDEX idx_delete ON test_attributes (to_block)
CREATE INDEX idx_entity_kv ON test_attributes (entity_key, key, from_block DESC)
```

---


### Parameters

| Parameter | Value |
|-----------|-------|
| Database | `:memory:` (in-memory SQLite) |
| Rows | 1,000,000 |
| Batch size | Full dataset (single `executemany`) |
| PRAGMA settings | Default |

### Results

#### Individual Inserts
| Configuration | Indexes | Time (s) | Ins/sec | Slowdown |
## Experiment 8: Commit Time Analysis with Real Arkiv Data (Sampled Blocks)

**Script:** `08_benchmark_sampled_blocks.py`

This experiment benchmarks commit times using 50,000 sampled blocks from real arkiv data, logging per-block commit time, number of entities, number of attributes, and payload size. The analysis is performed in the notebook [`commit_time_distribution.ipynb`](./commit_time_distribution.ipynb).

### Key Findings

- **Number of Attributes**: Strongest predictor of commit time (correlation coefficient: 0.96).
- **Number of Entities**: Also highly correlated (0.90).
- **Payload Size**: Weakly correlated (-0.06).

### Implications

- Commit time is dominated by the number of attributes per block.
- Payload size has minimal impact on commit time in this dataset.

---

## Experiment 9: Commit Time Analysis with Attribute Type Split

**Script:** `09_benchmark_sampled_blocks.py`

This experiment extends Experiment 8 by logging the number of string and numeric attributes per block separately, using the same 50,000 sampled blocks. Analysis is in [`commit_time_distribution_attrsplit.ipynb`](./commit_time_distribution_attrsplit.ipynb).

### Key Findings

- **String Attributes**: Show the strongest correlation with commit time (correlation coefficient: ~0.95).
- **Numeric Attributes**: Also correlated, but less strongly (~0.80).
- **Entities and Payload**: Similar trends as before; number of entities is correlated, payload size is not.

### Implications

- String attributes are the main driver of commit time in the arkiv schema.
- Optimizing the number and handling of string attributes can yield the greatest performance gains.

---

## Experiment 10: Commit Time Analysis with Simple EAV Schema

**Script:** `10_benchmark_sampled_blocks_simple_eav.py`

This experiment benchmarks the same 50,000 sampled blocks as Experiments 8 and 9, but uses a simple EAV schema (single block column, 2 indexes per attribute table). Analysis is in [`commit_time_distribution_simple_eav.ipynb`](./commit_time_distribution_simple_eav.ipynb).

### Key Findings

- **Commit times are lower overall** compared to the full arkiv schema, reflecting the reduced index and schema complexity.
- **String attributes remain the strongest predictor** of commit time, but the absolute times are reduced.
- **Correlation structure is similar**: string attributes > numeric attributes > entities > payload size.

### Implications

- The simple EAV schema is more efficient for write-heavy workloads, but the relative impact of string attributes persists.
- Schema simplification reduces absolute commit times, but does not change the main predictors.

---
|--------------|---------|----------|---------|----------|
| No indexes (reference) | 0 | 0.679 | 1,472,933 | 1.00x |
| Simple EVA (2 indexes) | 2 | 2.007 | 498,323 | 2.96x |
| Bi-temporal EVA (6 indexes) | 6 | 4.226 | 236,605 | 6.23x |

#### Batch Inserts (executemany)

| Configuration | Indexes | Time (s) | Ins/sec | Slowdown |
|--------------|---------|----------|---------|----------|
| No indexes (reference) | 0 | 0.507 | 1,971,555 | 1.00x |
| Simple EVA (2 indexes) | 2 | 1.819 | 549,828 | 3.59x |
| Bi-temporal EVA (6 indexes) | 6 | 3.999 | 250,085 | 7.88x |

### Key Finding

**Bi-temporal is ~2.1x slower than Simple EVA** (in-memory)

---

## Experiment 2: File-Based Database (Large Batches)

**Script:** `02_benchmark_indexes_file_batch10k.py`

### Parameters

| Parameter | Value |
|-----------|-------|
| Database | Temporary file (`tempfile`) |
| Rows | 10,000,000 |
| Batch size | 10,000 rows per commit |
| PRAGMA journal_mode | WAL |
| PRAGMA synchronous | NORMAL |

### Results

| Configuration | Indexes | Time (s) | Ins/sec | Slowdown |
|--------------|---------|----------|---------|----------|
| No indexes (reference) | 0 | 10.665 | 937,604 | 1.00x |
| Simple EVA (2 indexes) | 2 | 126.100 | 79,302 | 11.82x |
| Bi-temporal EVA (6 indexes) | 6 | 625.368 | 15,991 | 58.63x |

### Key Finding

**Bi-temporal is ~5.0x slower than Simple EVA** (file-based, large batches)

---

## Experiment 3: File-Based with Realistic Batch Size

**Script:** `03_benchmark_indexes_file_batch500.py`

This experiment simulates realistic arkiv usage where blocks contain ~100 entities with ~5 attributes each, resulting in ~500 attribute inserts per transaction.

### Parameters

| Parameter | Value |
|-----------|-------|
| Database | Temporary file (`tempfile`) |
| Rows | 10,000,000 |
| Batch size | 500 rows per commit (realistic arkiv batch) |
| PRAGMA journal_mode | WAL |
| PRAGMA synchronous | NORMAL |

### Results

| Configuration | Indexes | Time (s) | Ins/sec | Slowdown |
|--------------|---------|----------|---------|----------|
| No indexes (reference) | 0 | 10.852 | 921,466 | 1.00x |
| Simple EVA (2 indexes) | 2 | 446.218 | 22,411 | 41.12x |
| Bi-temporal EVA (6 indexes) | 6 | 1185.148 | 8,438 | 109.21x |

### Key Finding

**Bi-temporal is ~2.66x slower than Simple EVA** (file-based, realistic batches, executemany)

### Batch Size Impact

| Batch Size | Simple EVA Slowdown | Bi-temporal Slowdown | Bi-temp vs Simple |
|------------|---------------------|----------------------|-------------------|
| 10,000 | 12x | 59x | 5.0x |
| 500 | 41x | 109x | 2.66x |

Smaller batches dramatically amplify the baseline index overhead but the *relative* difference between Simple and Bi-temporal actually decreases. This is because frequent commits dominate the cost regardless of index count.

---

## Experiment 3b: File-Based with Individual Inserts (Arkiv-style)

**Script:** `03b_benchmark_indexes_file_batch500_individual.py`

Same as Experiment 3, but using individual `execute()` calls instead of `executemany` to match arkiv's actual insert pattern.

### Parameters

| Parameter | Value |
|-----------|-------|
| Database | Temporary file (`tempfile`) |
| Rows | 10,000,000 |
| Commit frequency | Every 500 rows |
| Insert mode | Individual `execute()` calls |
| PRAGMA journal_mode | WAL |
| PRAGMA synchronous | NORMAL |

### Results

| Configuration | Indexes | Time (s) | Ins/sec | Slowdown |
|--------------|---------|----------|---------|----------|
| No indexes (reference) | 0 | 12.433 | 804,299 | 1.00x |
| Simple EVA (2 indexes) | 2 | 443.997 | 22,523 | 35.71x |
| Bi-temporal EVA (6 indexes) | 6 | 1161.844 | 8,607 | 93.45x |

### Key Finding

**Bi-temporal is ~2.62x slower than Simple EVA** (individual inserts)

### Comparison: executemany vs Individual Inserts

| Configuration | executemany | Individual | Difference |
|--------------|-------------|------------|------------|
| No indexes | 10.9s (921K/s) | 12.4s (804K/s) | +14% |
| Simple EVA (2 idx) | 446s (22.4K/s) | 444s (22.5K/s) | ~0% |
| Bi-temporal (6 idx) | 1185s (8.4K/s) | 1162s (8.6K/s) | ~0% |

**Conclusion**: With indexes present, there is **no meaningful difference** between batch and individual inserts. The Python overhead is only visible in the no-index case (14%) but is completely masked by index maintenance overhead. This confirms that arkiv's individual insert pattern is not a performance concern.

---

## Experiment 4: Insert Mode Comparison

**Script:** `04_benchmark_insert_modes.py`

This experiment isolates the overhead of `executemany` (batch insert) vs individual `execute()` calls, with identical commit frequency. This tests whether arkiv's individual insert pattern has significant overhead compared to batching.

### Parameters

| Parameter | Value |
|-----------|-------|
| Database | Temporary file (`tempfile`) |
| Schema | Bi-temporal EVA (6 indexes) |
| Rows | 1,000,000 |
| Commit frequency | Every 500 rows (same for both modes) |
| PRAGMA journal_mode | WAL |
| PRAGMA synchronous | NORMAL |

### Results

| Mode | Time (s) | Ins/sec | Relative |
|------|----------|---------|----------|
| `executemany` (batch 500) + commit | 77.955 | 12,828 | 1.00x |
| Individual `execute()` × 500 + commit | 75.865 | 13,181 | 0.97x |

### Key Finding

**No meaningful difference** between batch and individual inserts (~3% variance, within noise).

### Implications for Arkiv (Go)

1. **Python FFI overhead is negligible** - The per-insert Python→C boundary cost is tiny compared to SQLite work
2. **Go would see equal or better results** - Go's CGo overhead is typically lower than Python's FFI
3. **Batching at application level is unnecessary** - SQLite's internal statement caching makes repeated `execute()` efficient
4. **The real bottleneck is index maintenance + disk I/O** - Not the insert mechanism

This confirms that arkiv's pattern of individual inserts within a transaction is fine. The performance problem is the 6 indexes, not the insert style.

---

## Analysis

### Why File-Based Shows Higher Overhead

| Factor | In-Memory | File-Based |
|--------|-----------|------------|
| Simple → Bi-temporal | 2.1x | **5.0x** |
| No idx → Simple EVA | 3x | **12x** |
| No idx → Bi-temporal | 6x | **59x** |

The dramatic increase in file-based overhead is due to:

1. **Disk I/O** - Index updates require random disk writes
2. **B-tree depth** - 10M rows = deeper trees = more page reads/writes per insert
3. **Page cache pressure** - 6 indexes compete for cache, causing more disk hits
4. **WAL checkpointing** - More data to sync to disk

### In-Memory vs File-Based

| Aspect | In-Memory | File-Based |
|--------|-----------|------------|
| Primary bottleneck | CPU | **Disk I/O** |
| Index overhead | Linear (~2x) | **Super-linear (~5x)** |
| Reason | Pure CPU work | Cache misses + random I/O |

### Conclusion

- **In-memory benchmarks** measure CPU overhead only (best case)
- **File-based benchmarks** reflect real-world production behavior
- The bi-temporal schema's extra indexes are **significantly more expensive at scale** when disk I/O is involved
- For write-heavy workloads, consider whether all 6 indexes are necessary

---

## Experiment 5: Full Arkiv Schema with Realistic Payloads

**Script:** `05_benchmark_arkiv_schema.py`

This experiment uses the exact arkiv schema with all 3 tables and 13 indexes, with realistic payload sizes.

### Schema

| Table | Indexes | Description |
|-------|---------|-------------|
| string_attributes | 6 | PK + 5 secondary indexes |
| numeric_attributes | 5 | PK + 4 secondary indexes |
| payloads | 2 | PK + 1 secondary index |
| **Total** | **13** | Full arkiv schema |

### Parameters

| Parameter | Value |
|-----------|-------|
| Database | Temporary file (`tempfile`) |
| Entities | 100,000 |
| Entities per block | 100 |
| Attribute key pool | 500 unique keys |
| PRAGMA journal_mode | WAL |
| PRAGMA synchronous | NORMAL |

### Results with Realistic Payloads

| Config | Str Attrs | Int Attrs | Payload | Time (s) | Entities/s | Rows/s |
|--------|-----------|-----------|---------|----------|------------|--------|
| Typical | 5 | 3 | 5 KB | 139.96 | 714 | 6,411 |
| Larger | 10 | 5 | 50 KB | 1172.46 | 85 | 1,355 |
| Minimal | 2 | 1 | 1 KB | 31.84 | 3,141 | 12,555 |

### Payload Size Impact

Comparing realistic payloads vs tiny (256 byte) payloads:

| Config | 256B Payload | Realistic Payload | Slowdown |
|--------|-------------|-------------------|----------|
| Typical (5KB) | 3,241 ent/s | 714 ent/s | **4.5x** |
| Larger (50KB) | 1,842 ent/s | 85 ent/s | **22x** |
| Minimal (1KB) | 6,849 ent/s | 3,141 ent/s | **2.2x** |

### Key Findings

1. **Payload size has massive impact** — 50KB payloads cause a **22x slowdown** compared to tiny payloads. This dwarfs the index overhead we measured earlier.

2. **Rows/sec drops with payload size** — Not just entities/sec, but actual row throughput decreases because each payload row requires more page writes.

3. **Realistic arkiv throughput**:
   - Typical entities (5KB payload): **~700 entities/sec**
   - Large entities (50KB payload): **~85 entities/sec**
   - Minimal entities (1KB payload): **~3,100 entities/sec**

4. **Total rows written** — With typical entities (5 str + 3 int attrs), each entity generates ~9 rows (5 string attrs + 3 numeric attrs + 1 payload).

### Implications

- **Payload optimization matters more than index optimization** for large entities
- For arkiv use cases with large payloads (>10KB), consider:
  - Storing payloads externally (filesystem/S3)
  - Compressing payloads before storage
  - Reducing payload frequency if possible
- Index overhead is still significant but secondary to payload I/O

---

## Experiment 6: Simple Schema (No Bi-temporality)

**Script:** `06_benchmark_arkiv_schema_simple.py`

This experiment compares the full bi-temporal arkiv schema against a simplified schema without temporal range queries.

### Schema Comparison

| Table | Bi-temporal (Exp 5) | Simple (Exp 6) |
|-------|---------------------|----------------|
| string_attributes | 6 indexes | 2 indexes |
| numeric_attributes | 5 indexes | 2 indexes |
| payloads | 2 indexes | 2 indexes |
| **Total** | **13 indexes** | **6 indexes** |

Key simplifications:
- Single `block` column instead of `from_block`/`to_block` range
- Simpler primary keys: `(entity_key, key)` instead of `(entity_key, key, from_block)`
- No temporal range indexes
- No delete indexes (no TTL support)

### Results (Simple Schema, 6 indexes)

| Config | Str Attrs | Int Attrs | Payload | Time (s) | Entities/s | Rows/s |
|--------|-----------|-----------|---------|----------|------------|--------|
| Typical | 5 | 3 | 5 KB | 135.97 | 735 | 6,599 |
| Larger | 10 | 5 | 50 KB | 1216.29 | 82 | 1,306 |
| Minimal | 2 | 1 | 1 KB | 31.87 | 3,137 | 12,543 |

### Comparison: Bi-temporal vs Simple

| Config | Bi-temporal (13 idx) | Simple (6 idx) | Speedup |
|--------|---------------------|----------------|---------|
| Typical (5KB) | 714 ent/s | 735 ent/s | **1.03x** |
| Larger (50KB) | 85 ent/s | 82 ent/s | **0.96x** |
| Minimal (1KB) | 3,141 ent/s | 3,137 ent/s | **1.00x** |

### Key Finding

**Removing 7 indexes made almost no difference!**

With realistic payload sizes, the index overhead becomes negligible. The I/O cost of writing 5-50KB BLOBs completely dominates the cost of maintaining extra indexes.

### Implications

1. **Keep bi-temporality** — The extra 7 indexes cost virtually nothing when payloads are involved
2. **Don't sacrifice features for performance** — Bi-temporal queries are valuable; the performance cost is negligible
3. **Index optimization only matters for tiny rows** — Our earlier experiments (2.6x slowdown) used small attribute-only data
4. **Focus on payload optimization** — This is the real bottleneck, not indexes

---

## Experiment 6: Commit Time Analysis

**Notebook:** [commit_time_distribution.ipynb](./commit_time_distribution.ipynb)

This experiment analyzes the factors influencing commit times in SQLite databases. Using a dataset of 50,000 blocks, we calculated the correlation between commit times and three key variables:

1. **Number of Entities**
2. **Payload Size (KB)**
3. **Number of Attributes**

### Key Findings

- **Number of Attributes**: The strongest predictor of commit times, with a correlation coefficient of 0.96.
- **Number of Entities**: Also highly correlated, with a coefficient of 0.90.
- **Payload Size**: Weakly correlated, with a coefficient of -0.06.

### Implications

- Optimizing the number of attributes per block can significantly reduce commit times.
- Payload size, while important in other contexts, has minimal impact on commit times in this dataset.

For detailed analysis and visualizations, refer to the linked notebook.

---

## Experiment 7: Memory/Cache Size Impact

**Script:** `07_benchmark_memory.py`

This experiment tests how Docker memory allocation and SQLite cache size affect insert performance with realistic large payloads.

### How to Run

#### Step 1: Set Docker Memory Limit

In Docker Desktop → Settings → Resources:
- Set "Memory limit" to desired value (e.g., 20 GB, 16 GB, 12 GB, 8 GB)
- Click "Apply & restart"
- Rebuild/restart devcontainer

#### Step 2: Verify Memory Inside Devcontainer

```bash
free -h
```

Expected output shows total memory matching your Docker setting:
```
               total        used        free      shared  buff/cache   available
Mem:            15Gi       2.1Gi        13Gi       ...
```

#### Step 3: Run Experiment

```bash
uv run python -m db.07_benchmark_memory <postfix>
```

Use a postfix matching your Docker limit:
```bash
uv run python -m db.07_benchmark_memory 20gb
uv run python -m db.07_benchmark_memory 16gb
uv run python -m db.07_benchmark_memory 12gb
uv run python -m db.07_benchmark_memory 8gb
```

Each run creates a separate database file in `data/benchmark_memory_test_<postfix>.db`.

### Parameters

| Parameter | Value |
|-----------|-------|
| Database | Fixed path (`data/benchmark_memory_test_{postfix}.db`) |
| Entities | 50,000 |
| Payload size | 50 KB (realistic large payload) |
| String attributes | 10 per entity |
| Numeric attributes | 7 per entity |
| Rows per entity | 18 (10 + 7 + 1 payload) |
| Commit frequency | Every 100 entities |
| Schema | Full arkiv bi-temporal (13 indexes) |
| SQLite cache_size | Auto: 50% of available RAM |
| PRAGMA journal_mode | WAL |
| PRAGMA synchronous | NORMAL |

### Results

| Docker RAM | free -h total | free -h avail | SQLite Cache | Duration | Entities/s | Rows/s | DB Size |
|------------|---------------|---------------|--------------|----------|------------|--------|---------|
| 20 GB | 19Gi | 14Gi | 7,559 MB | 721s | 69 | 1,238 | 2.7 GB |
| 16 GB | 15Gi | 13Gi | 6,842 MB | 663s | 75 | 1,348 | 2.7 GB |
| 12 GB | 11Gi | 9.5Gi | 4,861 MB | 672s | 74 | 1,330 | 2.7 GB |
| 8 GB | 8.0Gi | 5.9Gi | 3,018 MB | 667s | 75 | 1,340 | 2.7 GB |

### Key Finding

With 50KB payloads and 17 attributes per entity:
- **~69 entities/second** throughput
- **~1,238 rows/second** throughput  
- Database grows to ~2.7 GB for 50K entities

This confirms our earlier findings: **payload size dominates performance**. Even with 7.5 GB of SQLite cache, the bottleneck is writing 50KB BLOBs to disk, not index maintenance.

### Usage

```bash
# Run with postfix to create separate database files per configuration
uv run python -m db.07_benchmark_memory 20gb
uv run python -m db.07_benchmark_memory 16gb
uv run python -m db.07_benchmark_memory 12gb
uv run python -m db.07_benchmark_memory 8gb
```


## Experiment 8: Commit Time Analysis with Real Arkiv Data (Sampled Blocks)

**Script:** `08_benchmark_sampled_blocks.py`

This experiment benchmarks commit times using 50,000 sampled blocks from real arkiv data, logging per-block commit time, number of entities, number of attributes, and payload size. The analysis is performed in the notebook [`commit_time_distribution.ipynb`](./commit_time_distribution.ipynb).

### Blocktimes

```
Minimum Commit Time: 1 ms
Maximum Commit Time: 1523 ms
Percentiles:
0.00       1.0
0.25       3.0
0.50       4.0
0.75       6.0
1.00    1523.0
Name: commit_time_ms, dtype: float64

Correlation Matrix:
                num_entities  payload_kb  num_attributes  commit_time_ms
num_entities        1.000000   -0.079550        0.954896        0.901366
payload_kb         -0.079550    1.000000       -0.077705       -0.060844
num_attributes      0.954896   -0.077705        1.000000        0.963495
commit_time_ms      0.901366   -0.060844        0.963495        1.000000
The variable most correlated with commit time is: num_attributes
```

### Key Findings

- **Number of Attributes**: Strongest predictor of commit time (correlation coefficient: 0.96).
- **Number of Entities**: Also highly correlated (0.90).
- **Payload Size**: Weakly correlated (-0.06).

### Implications

- Commit time is dominated by the number of attributes per block.
- Payload size has minimal impact on commit time in this dataset.

---

## Experiment 9: Commit Time Analysis with Attribute Type Split

**Script:** `09_benchmark_sampled_blocks.py`

This experiment extends Experiment 8 by logging the number of string and numeric attributes per block separately, using the same 50,000 sampled blocks. Analysis is in [`commit_time_distribution_attrsplit.ipynb`](./commit_time_distribution_attrsplit.ipynb).

### Blocktimes

```
count    50000.000000
mean        21.332760
std         57.223565
min          0.000000
25%          3.000000
50%          4.000000
75%          6.000000
max        598.000000
Name: commit_time_ms, dtype: float64
Median: 4.0

Correlation of each variable with commit_time_ms:
commit_time_ms       1.000000
num_string_attrs     0.969025
num_numeric_attrs    0.936612
num_entities         0.915492
payload_kb          -0.056271
Name: commit_time_ms, dtype: float64
```

### Key Findings

- **String Attributes**: Show the strongest correlation with commit time (correlation coefficient: ~0.95).
- **Numeric Attributes**: Also correlated, but less strongly (~0.80).
- **Entities and Payload**: Similar trends as before; number of entities is correlated, payload size is not.

### Implications

- String attributes are the main driver of commit time in the arkiv schema.
- Optimizing the number and handling of string attributes can yield the greatest performance gains.

---

## Experiment 10: Commit Time Analysis with Simple EAV Schema

**Script:** `10_benchmark_sampled_blocks_simple_eav.py`

This experiment benchmarks the same 50,000 sampled blocks as Experiments 8 and 9, but uses a simple EAV schema (single block column, 2 indexes per attribute table). Analysis is in [`commit_time_distribution_simple_eav.ipynb`](./commit_time_distribution_simple_eav.ipynb).

### Blocktimes

```
Minimum Commit Time: 0 ms
Maximum Commit Time: 574 ms
Percentiles:
0.00      0.0
0.25      2.0
0.50      3.0
0.75      4.0
1.00    574.0
Name: commit_time_ms, dtype: float64

Correlation of each variable with commit_time_ms:
num_string_attrs     0.956558
num_numeric_attrs    0.912424
num_entities         0.877531
payload_kb          -0.058653
Name: commit_time_ms, dtype: float64
```

### Key Findings

- **Commit times are lower overall** compared to the full arkiv schema, reflecting the reduced index and schema complexity.
- **String attributes remain the strongest predictor** of commit time, but the absolute times are reduced.
- **Correlation structure is similar**: string attributes > numeric attributes > entities > payload size.

### Implications

- The simple EAV schema is more efficient for write-heavy workloads, but the relative impact of string attributes persists.
- Schema simplification reduces absolute commit times, but does not change the main predictors.

---

## Running the Benchmarks

```bash
# Experiment 1: In-memory benchmark (fast, ~1M rows)
uv run python -m db.01_benchmark_indexes_inmemory

# Experiment 2: File-based benchmark - large batches (slower, ~10M rows, batch=10K)
uv run python -m db.02_benchmark_indexes_file_batch10k

# Experiment 3: File-based benchmark - realistic batches (~10M rows, batch=500, executemany)
uv run python -m db.03_benchmark_indexes_file_batch500

# Experiment 3b: File-based benchmark - arkiv-style (~10M rows, batch=500, individual inserts)
uv run python -m db.03b_benchmark_indexes_file_batch500_individual

# Experiment 4: Insert mode comparison (executemany vs individual execute)
uv run python -m db.04_benchmark_insert_modes

# Experiment 5: Full arkiv schema with realistic payloads (bi-temporal, 13 indexes)
uv run python -m db.05_benchmark_arkiv_schema

# Experiment 6: Simple arkiv schema (no bi-temporality, 6 indexes)
uv run python -m db.06_benchmark_arkiv_schema_simple

# Experiment 7: Memory/cache size impact (50KB payloads, run at different Docker memory limits)
uv run python -m db.07_benchmark_memory 20gb

# Experiment 8: Commit time analysis with real arkiv data (sampled blocks)
uv run python -m db.08_benchmark_sampled_blocks data/arkiv-data-mendoza.db "" data/sampled_50k.db 50000

# Experiment 9: Commit time analysis with attribute type split
uv run python -m db.09_benchmark_sampled_blocks data/arkiv-data-mendoza.db "" data/sampled_50k_attrsplit.db 50000

# Experiment 10: Commit time analysis with simple EAV schema
uv run python -m db.10_benchmark_sampled_blocks_simple_eav data/arkiv-data-mendoza.db "" data/sampled_50k_simple_eav.db 50000
```
