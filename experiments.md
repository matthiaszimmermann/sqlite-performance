# SQLite Index Performance Experiments

This document captures benchmark results comparing insert performance across different index configurations.

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [Test Configurations](#test-configurations)
- [Experiment 2: File-Based Database (Large Batches)](#experiment-2-file-based-database-large-batches)
- [Experiment 3: File-Based with Realistic Batch Size](#experiment-3-file-based-with-realistic-batch-size)
- [Experiment 3b: File-Based with Individual Inserts (Arkiv-style)](#experiment-3b-file-based-with-individual-inserts-arkiv-style)
- [Experiment 4: Insert Mode Comparison](#experiment-4-insert-mode-comparison)
- [Analysis](#analysis)
- [Experiment 5: Full Arkiv Schema with Realistic Payloads](#experiment-5-full-arkiv-schema-with-realistic-payloads)
- [Experiment 6: Simple Schema (No Bi-temporality)](#experiment-6-simple-schema-no-bi-temporality)
- [Experiment 6: Commit Time Analysis](#experiment-6-commit-time-analysis)
- [Experiment 7: Memory/Cache Size Impact](#experiment-7-memorycache-size-impact)
- [Experiment 8: Commit Time Analysis with Real Arkiv Data (Sampled Blocks)](#experiment-8-commit-time-analysis-with-real-arkiv-data-sampled-blocks)
- [Experiment 9: Commit Time Analysis with Attribute Type Split](#experiment-9-commit-time-analysis-with-attribute-type-split)
- [Experiment 10: Commit Time Analysis with Simple EAV Schema](#experiment-10-commit-time-analysis-with-simple-eav-schema)
- [Running the Benchmarks](#running-the-benchmarks)
- [Database Scaling: 2x, 5x, 10x Mendoza](#database-scaling-2x-5x-10x-mendoza)
- [Read Performance: Measurement Points & Realistic Query Mixes](#read-performance-measurement-points--realistic-query-mixes)
- [Performance Testing Strategy: Scaling & Read/Write Limits](#performance-testing-strategy-scaling--readwrite-limits)
- [Data Center Benchmark: Controlled Test Environment](#data-center-benchmark-controlled-test-environment)

---

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

---

## Database Scaling: 2x, 5x, 10x Mendoza

This section details how to generate scaled databases for performance testing.

### Target Scale Points

| Scale | Entities | Rows | DB Size | Index Size (est) | Generation Time |
|-------|----------|------|---------|------------------|-----------------|
| **1x** (mendoza) | 800K | 20M | 13 GB | ~5 GB | baseline |
| **2x** | 1.6M | 40M | 27 GB | ~10 GB | ~2-3 hours |
| **5x** | 4.0M | 100M | 67 GB | ~25 GB | ~5-6 hours |
| **10x** | 8.0M | 200M | 133 GB | ~50 GB | ~10-12 hours |

### Scaling Approach Options

#### Option A: Duplicate with Fresh Entity Keys

The simplest approach — copy mendoza data N times with regenerated entity keys.

**Pros:**
- Fast to implement
- Preserves attribute distributions exactly
- Entity lifecycle patterns preserved (43% persistent, 56% ephemeral)

**Cons:**
- Artificial entity key distribution (clumped by generation batch)
- No temporal spread — all duplicates have similar block ranges
- May not stress B-tree splits realistically

**Method:**
```python
# For each entity in mendoza:
#   1. Generate new random entity_key (32 bytes)
#   2. Copy all string_attributes with new key
#   3. Copy all numeric_attributes with new key
#   4. Copy payload with new key
#   5. Optionally offset from_block/to_block to spread temporally
```

#### Option B: Temporal Extension

Extend the block range (currently ~1.15M blocks / 27 days) to simulate longer operation.

**Pros:**
- Realistic temporal distribution
- Better tests bi-temporal query patterns
- Simulates long-running production database

**Cons:**
- Doesn't test entity density at same block height
- Complex to maintain attribute version chains correctly

**Method:**
```python
# Extend block range from 1.15M to 11.5M blocks (10x = ~270 days)
# Distribute new entities across extended range
# Maintain from_block/to_block relationships
```

#### Option C: Hybrid (Recommended)

Combine both approaches for realistic scaling.

**Method:**
```python
# 1. Duplicate entities with fresh keys (N-1 copies)
# 2. Spread new entities temporally:
#    - 2x: blocks 1.15M → 2.3M
#    - 5x: blocks 1.15M → 5.75M
#    - 10x: blocks 1.15M → 11.5M
# 3. Maintain realistic attribute update patterns:
#    - 43% of new entities: long-lived (to_block = max)
#    - 56% of new entities: ephemeral (to_block = from_block + random(5min-5hr))
```

### Implementation: `11_generate_scaled_db.py`

```python
"""
Generate scaled databases (2x, 5x, 10x mendoza) for performance testing.

Usage:
    uv run python -m db.11_generate_scaled_db mendoza.db 2x scaled_2x.db
    uv run python -m db.11_generate_scaled_db mendoza.db 5x scaled_5x.db
    uv run python -m db.11_generate_scaled_db mendoza.db 10x scaled_10x.db
"""

import secrets
from dataclasses import dataclass

@dataclass
class ScaleConfig:
    multiplier: int
    block_offset_per_copy: int  # blocks to offset each copy
    
SCALE_CONFIGS = {
    "2x": ScaleConfig(2, 1_150_000),    # ~27 days per copy
    "5x": ScaleConfig(5, 1_150_000),
    "10x": ScaleConfig(10, 1_150_000),
}

def generate_fresh_entity_key() -> bytes:
    """Generate random 32-byte entity key."""
    return secrets.token_bytes(32)

def copy_entity_with_offset(
    src_cursor, dst_cursor, 
    old_key: bytes, new_key: bytes,
    block_offset: int
):
    """Copy all data for an entity with new key and block offset."""
    
    # Copy string_attributes
    src_cursor.execute("""
        SELECT from_block, to_block, key, value
        FROM string_attributes WHERE entity_key = ?
    """, (old_key,))
    for row in src_cursor:
        from_b, to_b, key, value = row
        # Update system keys
        if key == "$key":
            value = "0x" + new_key.hex()
        dst_cursor.execute("""
            INSERT INTO string_attributes 
            (entity_key, from_block, to_block, key, value)
            VALUES (?, ?, ?, ?, ?)
        """, (new_key, from_b + block_offset, to_b + block_offset, key, value))
    
    # Copy numeric_attributes (similar)
    # Copy payloads (similar, update cached $key in JSON)
```

### Verification Queries

After generating scaled DBs, verify data integrity:

```sql
-- Entity count
SELECT COUNT(DISTINCT entity_key) FROM string_attributes;

-- Row counts
SELECT 
    (SELECT COUNT(*) FROM string_attributes) as str_attrs,
    (SELECT COUNT(*) FROM numeric_attributes) as num_attrs,
    (SELECT COUNT(*) FROM payloads) as payloads;

-- Block range
SELECT MIN(from_block), MAX(to_block) FROM string_attributes;

-- Entity key uniqueness
SELECT COUNT(*), COUNT(DISTINCT entity_key) FROM payloads;
```

### Disk Space Requirements

| Scale | DB Size | Working Copy | Analysis Scripts | Total |
|-------|---------|--------------|------------------|-------|
| 1x | 13 GB | — | 2 GB | 15 GB |
| 2x | 27 GB | 27 GB | 2 GB | 56 GB |
| 5x | 67 GB | 67 GB | 2 GB | 136 GB |
| 10x | 133 GB | 133 GB | 2 GB | 268 GB |

**Recommendation:** Use SSD with ≥500 GB free space for 10x testing.

---

## Read Performance: Measurement Points & Realistic Query Mixes

### Measurement Points

#### Reads: Entities/sec

| Metric | Measurement | SQL Pattern |
|--------|-------------|-------------|
| **Entity fetch** | Complete entity retrieval | Join string + numeric attrs + payload by entity_key |
| **Attribute scan** | Entities matching criteria | WHERE key=? AND value=? on string_attributes |
| **Historical query** | Entity state at block N | WHERE entity_key=? AND from_block <= N AND to_block > N |

**Entity fetch benchmark:**
```sql
-- Full entity at current state (to_block = max)
SELECT s.key, s.value 
FROM string_attributes s
WHERE s.entity_key = ? AND s.to_block = 9223372036854775807;

SELECT n.key, n.value 
FROM numeric_attributes n
WHERE n.entity_key = ? AND n.to_block = 9223372036854775807;

SELECT payload, content_type 
FROM payloads 
WHERE entity_key = ? AND to_block = 9223372036854775807;
```

#### Writes: Rows/sec and Entities/sec

| Metric | Measurement | Notes |
|--------|-------------|-------|
| **Rows/sec** | Raw INSERT throughput | Best for comparing DB engines |
| **Entities/sec** | Full entity commits | Includes ~25 rows + payload per entity |

**Entity write composition (based on mendoza averages):**
- 15 string attributes → 15 INSERT
- 9 numeric attributes → 9 INSERT  
- 1 payload (~5KB) → 1 INSERT
- **Total: ~25 rows per entity**

At ~6,400 rows/sec → **~256 entities/sec** (mendoza baseline)

### Realistic Read Mix Proposals

Based on mendoza entity types and scenarios.md use cases, here are 3 read mix profiles:

#### Mix A: "Data Center Workload"

Simulates decentralized compute network operations (100K nodes, 500K workloads).

| Query Type | Weight | SQL Pattern | Rationale |
|------------|--------|-------------|-----------|
| **Node status lookup** | 30% | `entity_key = ?` on string_attrs | Check node availability |
| **Workload by status** | 25% | `key='status' AND value='pending'` | Find workloads to schedule |
| **Nodes by resource** | 20% | `key='allocatable_gpu' AND value > ?` | Find capable nodes |
| **Creator lookup** | 10% | `key='$creator' AND value=?` | Find tenant's entities |
| **Historical state** | 10% | `entity_key=? AND from_block<=? AND to_block>?` | Audit trail |
| **Payload fetch** | 5% | `SELECT payload WHERE entity_key=?` | Get config blobs |

**Characteristics:**
- Point lookups dominate (55%)
- Moderate attribute scans (45%)
- Historical queries rare (10%)
- Small result sets expected

#### Mix B: "Governance/Voting Platform"

Simulates DAO voting with proposal→vote relationships (from mendoza CivicCommit data).

| Query Type | Weight | SQL Pattern | Rationale |
|------------|--------|-------------|-----------|
| **Get proposal** | 20% | `entity_key = ?` | View proposal details |
| **Votes for proposal** | 25% | `key='proposalKey' AND value=?` | Count/list votes |
| **User's votes** | 15% | `key='$creator' AND value=?` on votes | User voting history |
| **Active proposals** | 15% | `key='status' AND value='active'` | List open votes |
| **Proposal by type** | 10% | `key='type' AND value='proposal'` | Filter by entity type |
| **Historical state** | 10% | `from_block<=? AND to_block>?` | Audit/dispute resolution |
| **Full entity fetch** | 5% | All attrs + payload | Detailed view |

**Characteristics:**
- Relationship queries dominate (40% = votes for proposal + user's votes)
- Type-based filtering common (25%)
- Historical queries for auditability (10%)
- Larger result sets (votes for proposal)

#### Mix C: "IoT/Streaming Data"

Simulates high-frequency sensor data or chunked media (from mendoza video-chunk, sensor_data).

| Query Type | Weight | SQL Pattern | Rationale |
|------------|--------|-------------|-----------|
| **Latest N entities by type** | 30% | `key='type' AND value=? ORDER BY from_block DESC LIMIT N` | Recent sensor readings |
| **Entity by key** | 20% | `entity_key = ?` | Fetch specific chunk |
| **Chunks in sequence** | 15% | `key='nextBlockId' AND value=?` | Follow chunk chain |
| **Time range query** | 15% | `from_block >= ? AND from_block < ?` | Data for time window |
| **Payload fetch** | 15% | `SELECT payload WHERE entity_key=?` | Get binary chunk data |
| **Creator's recent** | 5% | `key='$creator' AND value=? ORDER BY from_block DESC LIMIT 20` | User's uploads |

**Characteristics:**
- Temporal queries dominate (45%)
- Large payload fetches (15%)
- Sequential access patterns (chunk chains)
- High volume, low complexity queries

### Read Mix Comparison

| Aspect | Mix A: Data Center | Mix B: Governance | Mix C: IoT/Streaming |
|--------|-------------------|-------------------|----------------------|
| **Point lookups** | 55% | 25% | 35% |
| **Attribute scans** | 35% | 55% | 35% |
| **Temporal queries** | 10% | 10% | 45% |
| **Payload heavy** | Low (5%) | Low (5%) | High (15%) |
| **Result set size** | Small (1-10) | Medium (10-100) | Small-Medium |
| **Index pressure** | `entity_key`, `key+value` | `key+value` heavy | `from_block` heavy |

### Implementation: Query Generators

```python
"""Query generators for read benchmarks."""

import random
from dataclasses import dataclass
from typing import Callable

@dataclass 
class QueryMix:
    name: str
    queries: list[tuple[float, Callable]]  # (weight, generator)

def make_data_center_mix(entity_keys: list[bytes], creators: list[str]) -> QueryMix:
    """Mix A: Data Center workload queries."""
    return QueryMix(
        name="data_center",
        queries=[
            (0.30, lambda: (
                "SELECT key, value FROM string_attributes WHERE entity_key = ? AND to_block = 9223372036854775807",
                (random.choice(entity_keys),)
            )),
            (0.25, lambda: (
                "SELECT DISTINCT entity_key FROM string_attributes WHERE key = 'status' AND value = 'pending' LIMIT 100",
                ()
            )),
            (0.20, lambda: (
                "SELECT DISTINCT entity_key FROM numeric_attributes WHERE key = 'allocatable_gpu' AND value > ? LIMIT 50",
                (random.randint(0, 4),)
            )),
            (0.10, lambda: (
                "SELECT DISTINCT entity_key FROM string_attributes WHERE key = '$creator' AND value = ? LIMIT 100",
                (random.choice(creators),)
            )),
            (0.10, lambda: (
                "SELECT key, value FROM string_attributes WHERE entity_key = ? AND from_block <= ? AND to_block > ?",
                (random.choice(entity_keys), random.randint(1, 1_000_000), random.randint(1, 1_000_000))
            )),
            (0.05, lambda: (
                "SELECT payload FROM payloads WHERE entity_key = ? AND to_block = 9223372036854775807",
                (random.choice(entity_keys),)
            )),
        ]
    )

def make_governance_mix(entity_keys: list[bytes], proposal_keys: list[str], creators: list[str]) -> QueryMix:
    """Mix B: Governance/Voting workload queries."""
    return QueryMix(
        name="governance",
        queries=[
            (0.20, lambda: (
                "SELECT key, value FROM string_attributes WHERE entity_key = ? AND to_block = 9223372036854775807",
                (random.choice(entity_keys),)
            )),
            (0.25, lambda: (
                "SELECT DISTINCT entity_key FROM string_attributes WHERE key = 'proposalKey' AND value = ? LIMIT 500",
                (random.choice(proposal_keys),)
            )),
            (0.15, lambda: (
                "SELECT DISTINCT entity_key FROM string_attributes WHERE key = '$creator' AND value = ? AND to_block = 9223372036854775807 LIMIT 100",
                (random.choice(creators),)
            )),
            (0.15, lambda: (
                "SELECT DISTINCT entity_key FROM string_attributes WHERE key = 'status' AND value = 'active' LIMIT 100",
                ()
            )),
            (0.10, lambda: (
                "SELECT DISTINCT entity_key FROM string_attributes WHERE key = 'type' AND value = 'proposal' LIMIT 100",
                ()
            )),
            (0.10, lambda: (
                "SELECT key, value FROM string_attributes WHERE entity_key = ? AND from_block <= ? AND to_block > ?",
                (random.choice(entity_keys), random.randint(1, 1_000_000), random.randint(1, 1_000_000))
            )),
            (0.05, lambda: (
                """SELECT s.key, s.value, n.key, n.value, p.payload
                   FROM string_attributes s
                   LEFT JOIN numeric_attributes n ON s.entity_key = n.entity_key AND n.to_block = 9223372036854775807
                   LEFT JOIN payloads p ON s.entity_key = p.entity_key AND p.to_block = 9223372036854775807
                   WHERE s.entity_key = ? AND s.to_block = 9223372036854775807""",
                (random.choice(entity_keys),)
            )),
        ]
    )

def make_iot_streaming_mix(entity_keys: list[bytes], types: list[str], creators: list[str]) -> QueryMix:
    """Mix C: IoT/Streaming workload queries."""
    return QueryMix(
        name="iot_streaming", 
        queries=[
            (0.30, lambda: (
                "SELECT entity_key FROM string_attributes WHERE key = 'type' AND value = ? ORDER BY from_block DESC LIMIT 50",
                (random.choice(types),)
            )),
            (0.20, lambda: (
                "SELECT key, value FROM string_attributes WHERE entity_key = ? AND to_block = 9223372036854775807",
                (random.choice(entity_keys),)
            )),
            (0.15, lambda: (
                "SELECT entity_key FROM string_attributes WHERE key = 'nextBlockId' AND value = ?",
                ("chunk_" + str(random.randint(1, 10000)),)
            )),
            (0.15, lambda: (
                "SELECT DISTINCT entity_key FROM string_attributes WHERE from_block >= ? AND from_block < ? LIMIT 100",
                (random.randint(0, 900_000), random.randint(100_000, 1_000_000))
            )),
            (0.15, lambda: (
                "SELECT payload FROM payloads WHERE entity_key = ? AND to_block = 9223372036854775807",
                (random.choice(entity_keys),)
            )),
            (0.05, lambda: (
                "SELECT entity_key FROM string_attributes WHERE key = '$creator' AND value = ? ORDER BY from_block DESC LIMIT 20",
                (random.choice(creators),)
            )),
        ]
    )
```

### Benchmark Protocol

```
For each DB size (1x, 2x, 5x, 10x):
  For each mix (A, B, C):
    1. Warm up: 1000 queries (discard results)
    2. Measure: 10000 queries
       - Record latency for each query
       - Calculate: p50, p95, p99, max
       - Calculate: QPS (queries per second)
    3. Cool down: 5 seconds
    
Report:
  - QPS by mix and DB size
  - Latency distribution by query type
  - Index hit rates (if measurable)
```

### Expected Results Template

| DB Size | Mix | QPS | p50 (ms) | p95 (ms) | p99 (ms) | Notes |
|---------|-----|-----|----------|----------|----------|-------|
| 1x, 2x, 5x, 10x | A: Data Center | — | — | — | — | |
| 1x, 2x, 5x, 10x | B: Governance | — | — | — | — | |
| 1x, 2x, 5x, 10x | C: IoT | — | — | — | — | |

---

## Performance Testing Strategy: Scaling & Read/Write Limits

This section outlines the systematic approach to determine arkiv's performance limits at scale.

### Goals

1. **Speed to reliable findings** — Minimize time to actionable performance data
2. **Write limits at scale** — Measure write performance at 2x, 5x, 10x DB sizes (up to 133GB)
3. **Read limits** — Probe maximum read rates for realistic query patterns
4. **Combined limits** — Find sustainable read+write throughput with single-writer constraint

### Team Assignments

| Workstream | Owner | Duration | Deliverable |
|------------|-------|----------|-------------|
| **A: DB Generation** | SWE 1 | 2 days | 2x, 5x, 10x scaled databases |
| **B: Write Load Testing** | Supplier Team | 4 days | Write-only performance at all scales |
| **C: Read Benchmark** | SWE 2 | 3 days | Read-only performance at all scales |
| **D: Combined Testing** | SWE 1 + SWE 2 | 2 days | Combined read+write limits |
| **E: Analysis** | All | 1 day | Recommendations document |

### Timeline

```
Day 1: SWE 1 generates 2x, 5x DBs
       Supplier sets up test environment, discusses RAM config (8GB→16GB→32GB?)
       SWE 2 designs query mix + implements read benchmark

Day 2: SWE 1 generates 10x DB, hands off all DBs
       Supplier runs write benchmark on 1x
       SWE 2 runs read benchmark on 1x, 2x

Day 3: SWE 1 available for support
       Supplier runs write benchmark on 2x, 5x
       SWE 2 runs read benchmark on 5x, 10x

Day 4: Supplier runs write benchmark on 10x
       Buffer / re-runs

─── SYNC POINT ───

Day 5: SWE 1 + SWE 2 implement combined benchmark (pair programming)
       Supplier provides infrastructure support

Day 6: Combined tests on 2x, 5x, 10x DBs
       SWE 1: 2x, 5x | SWE 2: 10x

Day 7: Analysis and recommendations (all hands)
```

### Workstream A: DB Generation (SWE 1)

**Target Scale Points:**

| Scale | Entities | Rows | DB Size | Generation Time |
|-------|----------|------|---------|-----------------|
| 1x (mendoza) | 800K | 20M | 13 GB | baseline |
| 2x | 1.6M | 40M | 27 GB | ~2-3 hours |
| 5x | 4.0M | 100M | 67 GB | ~5-6 hours |
| 10x | 8.0M | 200M | 133 GB | ~10-12 hours |

**Method:** Extend `08_benchmark_sampled_blocks.py` to:
- Random sample blocks from mendoza
- Generate fresh entity keys (avoid conflicts)
- Append to create larger DBs incrementally (2x → 5x → 10x)

**Script:** `11_generate_scaled_db.py`

### Workstream B: Write Load Testing (Supplier Team)

**Environment:** Full node + DB setup (production-like)

**RAM Configuration Discussion:**

| DB Size | Index Size (est) | Min RAM | Recommended RAM |
|---------|------------------|---------|-----------------|
| 1x (13GB) | ~5GB | 8GB | 16GB |
| 2x (27GB) | ~10GB | 16GB | 16GB |
| 5x (67GB) | ~25GB | 16GB | 32GB |
| 10x (133GB) | ~50GB | 32GB | 64GB |

**Key Question:** Current 8GB may be insufficient. Test at 16GB and 32GB to find sweet spot.

**Metrics to Capture:**

| Metric | Target | Failure Threshold |
|--------|--------|-------------------|
| Commit latency p50 | <500ms | >2000ms (misses block) |
| Commit latency p99 | <1500ms | >2000ms |
| Commit latency max | <2000ms | >3000ms |
| Rows/sec throughput | >5000 | <2000 |

**Checkpoint Considerations:**

WAL checkpoints can cause latency spikes. Monitor and tune:

```sql
-- Increase checkpoint threshold to reduce frequency
PRAGMA wal_autocheckpoint = 10000;  -- ~40MB WAL before checkpoint
```

Or use manual PASSIVE checkpoints between blocks.

### Workstream C: Read Benchmark (SWE 2)

**Query Mix** (based on mendoza data + use cases):

| Query Type | % of Mix | SQL Pattern | Expected Use |
|------------|----------|-------------|--------------|
| Entity lookup | 40% | `WHERE entity_key = ?` | Get entity state |
| Attribute scan | 20% | `WHERE key = ? AND value = ?` | Find by type/status |
| Creator lookup | 15% | `WHERE key = '$creator' AND value = ?` | Find by owner |
| Block range | 15% | `WHERE from_block <= ? AND to_block > ?` | Historical queries |
| Payload fetch | 10% | `SELECT payload WHERE entity_key = ?` | Get binary data |

**Test Matrix:**

| DB Size | Connections | Target QPS Range |
|---------|-------------|------------------|
| 1x, 2x, 5x, 10x | 1 | 100 → 10K |
| 1x, 2x, 5x, 10x | 10 | 100 → 10K |

**Script:** `12_read_benchmark.py`

### Workstream D: Combined Read+Write Testing

**Protocol:**

```
For each DB size (2x, 5x, 10x):
  1. Baseline: writes only, measure commit latency
  2. Light reads: writes + 100 QPS reads
  3. Medium reads: writes + 1K QPS reads
  4. Heavy reads: writes + 5K QPS reads
  5. Find ceiling: binary search for max reads where writes stay <2sec
```

**Script:** `13_combined_benchmark.py`

### Verification Checklist

**Memory/Caching:**

| Check | Command/Method | Target |
|-------|----------------|--------|
| Index in RAM? | `vmtouch -v db.file` | >95% resident |
| Cache misses? | `iostat` during benchmark | <10 reads/sec |
| Checkpoint stalls? | Log p99/max latency | No commits >500ms |
| WAL growth? | Monitor WAL file size | <100MB typical |

**Pre-load DB into OS cache:**
```bash
vmtouch -t arkiv-data-mendoza.db  # Touch all pages
# Or: cat db.file > /dev/null
```

### Success Criteria

The experiments succeed if we can answer:

| Question | Data Needed |
|----------|-------------|
| Max writes at 10x? | rows/sec, latency p99 |
| Max reads at 10x? | QPS, latency p99 |
| Combined sweet spot? | X writes/sec + Y reads/sec |
| Degradation curve? | Performance vs DB size chart |
| V1 feasibility? | Go/no-go for Data Center (621K entities, 10K-50K writes/sec, 1K-10K reads/sec) |

### Potential Optimizations to Test

If baseline results are insufficient:

| Optimization | Expected Impact | Trade-off |
|--------------|-----------------|-----------|
| **Reduce indexes** (13→6) | ~30-40% write speedup | Slower reads on some patterns |
| **Increase RAM** | Fewer cache misses | Cost |
| **Tune checkpoints** | Fewer latency spikes | Larger WAL, longer recovery |
| **Drop bi-temporal** | ~20% write speedup | Lose historical queries |
| **Cap writes/block** | Predictable latency | Lower throughput |

### Scripts Needed

| Script | Owner | Purpose |
|--------|-------|---------|
| `11_generate_scaled_db.py` | SWE 1 | Generate 2x, 5x, 10x DBs |
| `12_read_benchmark.py` | SWE 2 | Read-only performance testing |
| `13_combined_benchmark.py` | SWE 1 + SWE 2 | Combined read+write testing |
| `14_analyze_results.py` | Any | Generate charts and recommendations |

### SQLite Alternatives

If SQLite optimizations prove insufficient, consider these alternatives in order:

| # | Database | Days | Rationale |
|---|----------|------|-----------|
| 1 | **libSQL** | 1-2 | Drop-in SQLite replacement, specifically targets checkpoint storms. Lowest migration risk. |
| 2 | **Embedded Postgres** | 3-5 | Proven at scale, background checkpoints, MVCC. Accept heavier footprint. |
| 3 | **DuckDB** | 2-3 | Append-only model fits DELETE+INSERT pattern. Worth validating for our workload. |

**Skip:**
- **LMDB / RocksDB** — KV-only, no SQL. Would require building custom query engine.

#### Success Criteria

```
p99 commit latency <500ms at 5x mendoza scale (67GB DB)
with sustained mendoza-like write load (~6K rows/sec)
```

#### Key Validation Test

Simulate entity update workload (worst case for B-tree reorg):

```
Reorg simulation:
  - DELETE 500 rows (existing PKs)
  - INSERT 500 rows (same PKs, new values)
  - Repeat for 1 hour

Measure:
  - p99 latency (must stay <500ms)
  - Storage bloat over time
  - Checkpoint/vacuum impact
```

#### Alternative Details

**libSQL (Turso)**
- Fork of SQLite with improved WAL handling
- Same API/bindings as SQLite — minimal code changes
- Specifically designed to reduce checkpoint storms
- Go: `github.com/tursodatabase/libsql-client-go/libsql`

**Embedded Postgres**
- Run Postgres as subprocess managed by application
- Go: `github.com/fergusstrange/embedded-postgres`
- Key advantage: **Background checkpoint workers** — checkpoints don't block commits

| Aspect | SQLite | Embedded Postgres |
|--------|--------|-------------------|
| Checkpoint | Blocks commit path | Background process (async) |
| Binaries | ~1MB | ~100-150MB |
| RAM (base) | ~10MB | ~100-200MB |
| Startup | ~0ms | ~1-3 seconds |
| Deployment | Single file | Data directory + processes |

Postgres tuning for embedded use:
```sql
shared_buffers = 256MB
checkpoint_timeout = 5min
checkpoint_completion_target = 0.9
max_wal_size = 1GB
```

**DuckDB**
- Columnar, append-optimized storage
- Different architecture might avoid B-tree reorg issues
- Optimized for OLAP (bulk loads) — OLTP performance unknown
- Go: `github.com/marcboeker/go-duckdb`

#### Decision Tree

```
SQLite p99 latency <500ms at 5x scale?
├─ YES → Stay with SQLite
└─ NO → Test libSQL (1-2 days)
         │
         libSQL works? → Done (minimal migration)
         ↓ No
         Test Embedded Postgres (3-5 days)
         │
         Postgres works? → Accept operational overhead
         ↓ No
         Test DuckDB (2-3 days)
         │
         DuckDB works? → Unexpected win
         ↓ No
         Revisit architecture (sharding, async writes, etc.)
```

---

## Data Center Benchmark: Controlled Test Environment

This section describes a controlled benchmark environment using synthetic Data Center data, enabling verifiable read/write performance testing at scale.

### Motivation

| Problem with Mendoza-only | Solution with Controlled Data |
|---------------------------|-------------------------------|
| Can't verify query results | **Know** expected results (deterministic IDs) |
| Random entity sampling | Query data we created |
| Generic query patterns | Use-case-specific patterns |
| Hard to isolate DC workload | DC data as distinct, queryable subset |

### Data Model: 4 Entity Types

```
┌─────────────────────────────────────────────────────────────┐
│                    Data Center Model                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   Node (100K per DC)              Workload (500K per DC)    │
│   ├── dc_id ◄─────────────────────┤── dc_id                 │
│   ├── region                       ├── status               │
│   ├── status                       ├── assigned_node ──────►│
│   ├── vm_type                      ├── region               │
│   ├── cpu_count                    ├── vm_type              │
│   ├── ram_gb                       ├── req_cpu              │
│   ├── price_hour                   ├── req_ram              │
│   └── avail_hours                  └── max_hours            │
│         │                                │                  │
│         ▼                                ▼                  │
│   NodeEvent                        WorkloadEvent            │
│   ├── node_id ──────────────────►  ├── workload_id ────────►│
│   ├── event_type                   ├── event_type           │
│   ├── old_status                   ├── old_status           │
│   ├── new_status                   ├── new_status           │
│   └── block                        └── block                │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Entity Attributes

#### Node (4 string + 4 numeric + 10KB payload)

| Attribute | Type | Values | Purpose |
|-----------|------|--------|---------|
| `dc_id` | string | `dc_01` ... `dc_21` | Data center identifier |
| `region` | string | `eu-west`, `us-east`, `asia-pac` | Geographic filtering |
| `status` | string | `available`, `busy`, `offline` | Schedulability |
| `vm_type` | string | `cpu`, `gpu`, `gpu_large` | Capability matching |
| `cpu_count` | numeric | 4, 8, 16, 32 | Resource filtering |
| `ram_gb` | numeric | 16, 32, 64, 128 | Resource filtering |
| `price_hour` | numeric | 50-500 (cents) | Cost optimization |
| `avail_hours` | numeric | 1, 4, 8, 24, 168 | Availability window |
| payload | blob | 10 KB random | Config/metadata blob |

#### Workload (5 string + 3 numeric + 10KB payload)

| Attribute | Type | Values | Purpose |
|-----------|------|--------|---------|
| `dc_id` | string | `dc_01` ... `dc_21` | Data center scope |
| `status` | string | `pending`, `running`, `completed`, `failed` | Lifecycle |
| `assigned_node` | string | `node_000001` or empty | Relationship |
| `region` | string | `eu-west`, `us-east`, `any` | Placement constraint |
| `vm_type` | string | `cpu`, `gpu`, `gpu_large` | Required capability |
| `req_cpu` | numeric | 1, 2, 4, 8 | Resource request |
| `req_ram` | numeric | 4, 8, 16, 32 | Resource request |
| `max_hours` | numeric | 1, 2, 4, 8, 24 | Max runtime |
| payload | blob | 10 KB random | Job spec blob |

### Deterministic ID Scheme

```python
# Nodes: node_{dc}_{n:06d}
def node_id(dc: int, n: int) -> str:
    return f"node_{dc:02d}_{n:06d}"  # e.g., node_07_000042

# Workloads: wl_{dc}_{w:06d}
def workload_id(dc: int, w: int) -> str:
    return f"wl_{dc:02d}_{w:06d}"    # e.g., wl_07_000123

# Derived relationships (deterministic):
def workload_node(dc: int, w: int) -> str:
    """Workload w is assigned to node (w % 100000) + 1 in same DC."""
    n = (w % 100000) + 1
    return node_id(dc, n)

# So we KNOW:
#   wl_07_000001 → node_07_000001
#   wl_07_100001 → node_07_000001 (5 workloads per node)
#   wl_07_000042 → node_07_000042
```

### Scale Points via Data Centers

| Target | DCs | Nodes | Workloads | Entities | Est. Size |
|--------|-----|-------|-----------|----------|-----------|
| **~1x mendoza** | 2 | 200K | 1M | 1.2M | ~13 GB |
| **~2x mendoza** | 4 | 400K | 2M | 2.4M | ~27 GB |
| **~5x mendoza** | 11 | 1.1M | 5.5M | 6.6M | ~67 GB |
| **~10x mendoza** | 21 | 2.1M | 10.5M | 12.6M | ~133 GB |

Each DC contributes ~6.3 GB (100K nodes + 500K workloads × ~10.5 KB each).

### Query Patterns (Verifiable)

All queries scope to a single DC, testing index selectivity at scale:

| Query | SQL Pattern | Expected Result |
|-------|-------------|-----------------|
| **Nodes in DC** | `dc_id = 'dc_07' AND type = 'node'` | Exactly 100K |
| **Available GPU nodes** | `dc_id = 'dc_07' AND status = 'available' AND vm_type = 'gpu'` | ~17.5K (70% avail × 25% GPU) |
| **Workloads on node** | `assigned_node = 'node_07_000042'` | Exactly 5 |
| **Pending workloads** | `dc_id = 'dc_07' AND status = 'pending'` | ~75K (15% of 500K) |
| **Node-workload match** | Multi-filter (see below) | Computable |

#### Workload→Node Matching Query

```sql
-- Find available nodes in DC 07 that can run a GPU workload for 8 hours
SELECT DISTINCT s1.entity_key 
FROM string_attributes s1
JOIN string_attributes s2 ON s1.entity_key = s2.entity_key
WHERE s1.key = 'workload_id' AND s2.key = 'assigned_node' AND s2.value != ''
LIMIT 10;
```

### Scripts

See [scripts.md](scripts.md) for detailed documentation on:
- `generate_dc_seed.py` — Seed database generator
- `inspect_dc_db.py` — Database inspector
- Manual database inspection with SQLite CLI

### Benchmark Combinations

#### Write Performance

| Mix | Composition | Tests |
|-----|-------------|-------|
| **Mendoza-only** | 100% sampled mendoza | Baseline (existing) |
| **DC-only** | 100% DC load events | Pure use-case writes |
| **DC-light** | 30% DC + 70% mendoza | DC as minority workload |
| **DC-heavy** | 70% DC + 30% mendoza | DC as dominant workload |

#### Read Performance

Against known DC data with verifiable results:

| Query Type | Weight | Verifiable? |
|------------|--------|-------------|
| Nodes by DC | 25% | ✅ Exactly 100K per DC |
| Available nodes | 20% | ✅ ~70K per DC |
| Workloads on node | 20% | ✅ Exactly 5 |
| Node-workload match | 20% | ✅ Computable |
| Historical state | 15% | ✅ If events generated |

#### Combined Read+Write

```
Phase 1: Load DC seed (offline, not timed)
Phase 2: Start write load (DC events)
Phase 3: Fire read queries against known DC data
Phase 4: Verify results match expectations
Phase 5: Record latencies for both reads and writes
```

### Next Steps

1. ✅ **Implement `generate_dc_seed.py`** — Initial state generator (done)
2. **Implement `generate_dc_load.py`** — Event stream generator for ongoing operations
3. **Implement DC read benchmark** — Verifiable queries against known data
4. **Run baseline tests** — DC-only at 2x scale to validate approach
5. **Integrate with existing benchmarks** — Mix DC + mendoza for combined tests

