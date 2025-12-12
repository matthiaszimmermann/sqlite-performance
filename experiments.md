# SQLite Index Performance Experiments

This document captures benchmark results comparing insert performance across different index configurations.

## Executive Summary

### Key Conclusions

1. **Index count dominates insert performance** — Going from 2 to 6 indexes causes a **2.6x slowdown** in realistic conditions (file-based, 500-row batches, individual inserts).

2. **Insert mechanism doesn't matter** — `executemany` (batch) vs individual `execute()` calls show **no measurable difference** when indexes are present. The Python/Go distinction is irrelevant for arkiv's performance.

3. **Commit frequency is critical** — Smaller batches (500 vs 10,000 rows per commit) dramatically increase absolute overhead but don't change the relative index impact much.

4. **File-based is much slower than in-memory** — Real disk I/O amplifies index overhead from ~2x (in-memory) to ~2.6-5x (file-based), depending on batch size.

### Arkiv-Specific Findings

| Metric | Value |
|--------|-------|
| Bi-temporal insert rate | ~8,600 rows/sec |
| Simple EVA insert rate | ~22,500 rows/sec |
| Potential speedup (fewer indexes) | **2.6x** |

### Recommendations

1. **Review index necessity** — Each of the 5 secondary indexes in the bi-temporal schema has a cost. Are all query patterns actually used?

2. **Consider deferred indexing** — For bulk imports, create indexes after data load.

3. **Batch size is fine** — Arkiv's ~500 attributes/block commit pattern is reasonable; larger batches would help but aren't critical.

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

## Experiment 1: In-Memory Database

**Script:** `01_benchmark_indexes_inmemory.py`

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
```
