# Mendoza Database Analysis

The **mendoza dataset** serves as our primary benchmark for arkiv capacity planning. It represents real-world arkiv data and establishes baseline performance metrics.

---

## Table of Contents

- [Dataset Characteristics](#dataset-characteristics)
- [Database Schema](#database-schema)
- [Entity Ownership](#entity-ownership)
- [Entity Diversity](#entity-diversity)
- [Entity Lifecycle](#entity-lifecycle)
- [Entity Relationships](#entity-relationships)
- [Storage Breakdown](#storage-breakdown)
- [Attribute Distribution](#attribute-distribution)
- [Payload Size Distribution](#payload-size-distribution)
- [Performance Metrics](#performance-metrics)
- [Scaling Multipliers](#scaling-multipliers)

---

## Dataset Characteristics

| Metric | Value | Notes |
|--------|-------|-------|
| **Entities** | 800,000 | Unique entity IDs |
| **String Attributes** | 12,100,000 | ~15 per entity avg |
| **Numeric Attributes** | 7,300,000 | ~9 per entity avg |
| **Payloads** | 1,070,000 | Binary blobs |
| **Total Rows** | ~20.5M | Across all tables |
| **Database Size** | 13.35 GB | SQLite file on disk |
| **Block Range** | 661 → 1,154,126 | ~1.15M blocks span |
| **Time Span** | ~27 days | Blocks produced every 2 seconds |
| **Unique Creators** | 195 | Distinct wallet addresses |

---

## Database Schema

```sql
CREATE TABLE string_attributes (
    entity_key BLOB NOT NULL,
    from_block INTEGER NOT NULL,
    to_block INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (entity_key, key, from_block)
);

CREATE TABLE numeric_attributes (
    entity_key BLOB NOT NULL,
    from_block INTEGER NOT NULL,
    to_block INTEGER NOT NULL,
    key TEXT NOT NULL,
    value INTEGER NOT NULL,
    PRIMARY KEY (entity_key, key, from_block)
);

CREATE TABLE payloads (
    entity_key BLOB NOT NULL,
    from_block INTEGER NOT NULL,
    to_block INTEGER NOT NULL,
    payload BLOB NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    string_attributes TEXT NOT NULL DEFAULT '{}',  -- cached JSON
    numeric_attributes TEXT NOT NULL DEFAULT '{}', -- cached JSON
    PRIMARY KEY (entity_key, from_block)
);

CREATE TABLE last_block (
    id INTEGER NOT NULL DEFAULT 1 CHECK (id = 1),
    block INTEGER NOT NULL,
    PRIMARY KEY (id)
);
```

**Key Design Points:**

- **Bi-temporal storage**: The `from_block`/`to_block` columns track validity periods, enabling full history of all entity changes throughout their lifetime. Every attribute/payload modification creates a new row with updated block range.

- **EAV schema**: Entity-Attribute-Value pattern allows flexible, schema-less metadata per entity without migrations.

- **Cached attributes**: The `payloads` table denormalizes attributes as JSON for efficient single-row entity retrieval at a specific block height.

---

## Entity Ownership

Distribution of entities by creator (wallet address).

| Creator | Entities | % | Cumulative % |
|---------|----------|---|-------------|
| `0x00000000...4429d8` | 449,531 | 42.0% | 42.0% |
| `0xf46e23f6...36f0` | 272,382 | 25.4% | 67.5% |
| `0x4144a13c...0f07` | 158,493 | 14.8% | 82.3% |
| `0x9192bad1...5af82` | 76,281 | 7.1% | 89.4% |
| `0x33f85522...965a` | 28,191 | 2.6% | 92.1% |
| `0xb2bee6f9...8fb1` | 22,599 | 2.1% | 94.2% |
| `0x196b09ff...e609` | 19,407 | 1.8% | 96.0% |
| *(187 others)* | ~43,000 | 4.0% | 100.0% |

**Key Insight**: Data is highly concentrated—the top 3 creators account for **82%** of all entities. This likely reflects:
- Golem provider registry (449K entities from one address)
- Demo/test applications
- A few active dApps

---

## Entity Diversity

Despite volume being dominated by a few large producers, the mendoza dataset contains **rich entity type diversity** from ~40 hackathon projects over the data collection period.

### Entity Type Distribution

| Metric | Value |
|--------|-------|
| **Distinct `type` values** | 134 |
| **Unique app prefixes** | ~95 |
| **Small projects (10-500 entities)** | 69 types |
| **Large projects (>500 entities)** | ~10 types |

### Top Entity Types by Volume

| Type | Entities | Source |
|------|----------|--------|
| *(no type)* | ~735,000 | Golem providers (system attr only) |
| `image` | 52,168 | CCats/CDogs AI images |
| `arkmon_test` | 3,538 | Test framework |
| `agent-report` | 3,414 | Golem agent reports |
| `video-chunk` | 2,366 | Video streaming app |
| `file-chunk` | 1,174 | File upload chunking |
| `watson`/`sherlock`/`moriarty` | ~2,400 | Mystery game entities |

### Hackathon Project Diversity (10-500 entities each)

Representative sample of smaller projects demonstrating real-world usage patterns:

| Category | Types | Examples |
|----------|-------|----------|
| **Governance** | 3 | `proposal` (159), `vote` (309), `opengov-proposal` (32) |
| **Chat/Messaging** | 8 | `chatMessage` (122), `chat_message` (83), `chat_room` (17) |
| **Media** | 6 | `hls-chunk` (222), `video-stream` (62), `song-metadata` (23) |
| **IoT/Sensors** | 3 | `sensor_data` (97), `device-beacon` (78), `Weather` (15) |
| **DeFi/Trading** | 4 | `market_snapshot` (432), `ask` (54), `offer` (29) |
| **Identity** | 5 | `user` (25), `profile` (16), `business-card` (14) |
| **Gaming** | 4 | `game-event` (18), `score_update` (47), `game-lobby` (4) |
| **Documents** | 4 | `inheritance-asset` (19), `invoice` (10), `statement` (11) |

> **Key Finding**: The dataset represents a healthy hackathon ecosystem with diverse use cases, not just mass-produced registry data.

---

## Entity Lifecycle

Analysis of entity creation and update patterns over the 27-day period.

### Entity Creation Over Time

Entities were created continuously via 2-second block production, not bulk-loaded:

| Day | Entities Created | Activity Level |
|-----|------------------|----------------|
| 0 | 171,331 | Initial load / genesis |
| 1-4 | ~75K | Ramp-up |
| **5-14** | **~500K** | **Peak hackathon activity** |
| 15-27 | ~50K | Steady-state |

Peak days saw 40K-85K entities/day, demonstrating **continuous live usage**.

### Entity Lifespan Distribution

| Lifespan | Entities | % | Interpretation |
|----------|----------|---|----------------|
| <30 min | 151,753 | 19% | Short-lived test/demo entities |
| 30 min - 5.5 hours | 293,932 | 37% | Session-based entities |
| 5.5 hours - 2.3 days | 6,662 | 0.8% | Medium-term entities |
| 2.3 - 11.5 days | 2,588 | 0.3% | Longer-lived entities |
| **>11.5 days** | **345,400** | **43%** | **Persistent entities** |

**Bimodal pattern**: ~43% are long-lived (registries, profiles), ~56% are ephemeral (sessions, messages, tests).

### Entity Updates

| Metric | Value | Notes |
|--------|-------|-------|
| **Entities with attribute updates** | 25,115 | 3.1% of all entities |
| **Single-version entities** | 777,186 | 97% created once, never updated |
| **Average versions per entity** | 15.14 | Across all attributes |

#### Most Frequently Updated Attributes

| Key | Entities | Avg Versions | Use Case |
|-----|----------|--------------|----------|
| `efficiency` | 182,163 | 2.47 | Golem provider metrics |
| `lastJobDate` | 182,163 | 2.47 | Golem job tracking |
| `speed` | 182,163 | 2.47 | Golem performance |
| `ping_count` | 20 | 3.05 | Health monitoring |
| `lastScoreDate` | 10 | 3.9 | Gaming leaderboards |

#### Golem Provider Update Frequency

| Update Pattern | Providers | % |
|----------------|-----------|---|
| No updates (1 version) | 159,162 | 87% |
| 2 versions | 6,427 | 3.5% |
| 3-5 versions | 5,510 | 3% |
| 6-10 versions | 3,865 | 2% |
| >10 versions | 7,199 | 4% |

> **Key Finding**: Most entities are create-once (registrations, events), but ~25K entities show real update activity with Golem providers being the primary source of attribute mutations.

---

## Entity Relationships

Entities in mendoza store relationships via string attributes containing entity keys. This pattern is common but has limitations.

### Relationship Patterns Found

| Pattern | Key | Count | Example |
|---------|-----|-------|---------|
| **Order→Provider** | `orderId`, `provider` | 7,502 | Golem job assignments |
| **Vote→Proposal** | `proposalKey` | 309 | Governance voting |
| **Message→Room** | `chatBaseKey`, `roomKey` | ~200 | Chat applications |
| **Chunk→Next** | `nextBlockId`, `dataEntityId` | 420 | Linked data chunks |
| **Session refs** | `sessionKey`, `sessionId` | ~180 | Session management |
| **DAO membership** | `daoKey` | 15 | DAO member entities |
| **Metric refs** | `metricKey` | 77 | Performance tracking |

### Relationship Examples

**Governance (CivicCommit)**:
```
proposal (159 entities) ← vote (309 entities, each has proposalKey)
```

**Chat Applications**:
```
chatBase (7 entities) ← chatMessage (122 entities, each has chatBaseKey)
chat_room (17 entities) ← chat_invite (16 entities, each has roomKey)
```

**Chunked Data**:
```
chunk[0] → chunk[1] → chunk[2] ... (via nextBlockId)
```

### Limitations of Current Approach

| Issue | Impact |
|-------|--------|
| **No referential integrity** | Can reference non-existent entities |
| **No reverse lookup index** | "Find all votes for proposal X" requires full scan |
| **String comparison** | Entity keys stored as hex strings, not blobs |
| **No cascade operations** | Deleting parent doesn't affect children |

### Proposed: First-Class Entity Relations

A dedicated `entity_relations` table would address these limitations:

```sql
CREATE TABLE entity_relations (
    entity_key BLOB NOT NULL,
    from_block INTEGER NOT NULL,
    to_block INTEGER NOT NULL,
    key TEXT NOT NULL,               -- relationship name
    target_entity_key BLOB NOT NULL, -- referenced entity
    PRIMARY KEY (entity_key, key, target_entity_key, from_block)
);
CREATE INDEX idx_reverse ON entity_relations(target_entity_key, key);
```

**Benefits**:
- Efficient reverse lookups ("all votes for this proposal")
- Referential integrity validation
- Proper BLOB comparison for entity keys
- Bi-temporal relationship history

---

## Storage Breakdown

| Component | Size | % of DB | Notes |
|-----------|------|---------|-------|
| **Data (tables)** | 7.46 GB | 60% | Actual row data |
| **Indexes** | 4.98 GB | 40% | Query optimization |
| **Total** | 12.44 GB | 100% | Used space |

### By Table

| Table | Data | Indexes | Total | Rows |
|-------|------|---------|-------|------|
| `payloads` | 6.2 GB | 0.1 GB | 6.3 GB | 1.1M |
| `string_attributes` | 1.0 GB | 3.6 GB | 4.6 GB | 12.1M |
| `numeric_attributes` | 0.5 GB | 1.4 GB | 1.9 GB | 7.3M |

> **Note**: `payloads` dominates storage (48.7%) due to large binary blobs. Attribute tables have high index overhead (~3-4x data size) due to multiple composite indexes for query patterns.

---

## Attribute Distribution

### String Attributes per Entity

| Attrs per Entity | Entity Count | % |
|------------------|--------------|---|
| 2-5 | 246,904 | 30.9% |
| 6-10 | 367,154 | 45.9% |
| 11-15 | 681 | 0.1% |
| 16-20 | 185,596 | 23.2% |

**Average**: ~15 string attributes per entity

### Numeric Attributes per Entity

| Attrs per Entity | Entity Count | % |
|------------------|--------------|---|
| 2-5 | 291,143 | 36.4% |
| 6-10 | 509,192 | 63.6% |

**Average**: ~9 numeric attributes per entity

### Most Common Attribute Keys

**String Attributes** (12.1M rows):

| Key | Count | Notes |
|-----|-------|-------|
| `$creator` | 1,069,993 | System: entity creator address |
| `$key` | 1,069,993 | System: entity unique key |
| `$owner` | 1,069,993 | System: current owner address |
| `name` | 449,761 | Golem provider names |
| `efficiency` | 449,517 | Golem provider metrics |
| `provId` | 449,517 | Golem provider ID |
| `project` | 272,108 | Project identifier |
| `EthDemo_dataType` | 272,056 | Demo app data |

**Numeric Attributes** (7.3M rows):

| Key | Count | Notes |
|-----|-------|-------|
| `$createdAtBlock` | 1,069,993 | System: creation block |
| `$expiration` | 1,069,993 | System: TTL block |
| `$opIndex` | 1,069,993 | System: operation index |
| `$sequence` | 1,069,993 | System: global sequence |
| `$txIndex` | 1,069,993 | System: transaction index |
| `group` | 449,517 | Golem provider group |
| `numberOfJobs` | 449,517 | Golem job count |

### Cached Attribute Size Distribution

The `payloads` table caches `string_attributes` and `numeric_attributes` as JSON strings.

#### String Attributes JSON Length (bytes)

| Bucket | Count | Notes |
|--------|-------|-------|
| 100-200 | 47 | Minimal metadata |
| 200-300 | 266,187 | Simple entities |
| 300-400 | 349,233 | Typical entities |
| 400-500 | 1,089 | |
| 500-750 | 453,437 | Rich metadata (largest bucket) |

#### Numeric Attributes JSON Length (bytes)

| Bucket | Count |
|--------|-------|
| 50-100 | 2,128 |
| 100-150 | 364,714 |
| 150-200 | 703,151 |

#### Entity Types by Attribute Size

```sql
SELECT json_extract(string_attributes, '$.type') as entity_type, COUNT(*), 
       AVG(length(string_attributes)), MAX(length(string_attributes))
FROM payloads GROUP BY entity_type ORDER BY COUNT(*) DESC;
```

| type | count | avg str_len | max str_len |
|------|-------|-------------|-------------|
| *(null)* | 999,174 | 447 | 660 |
| image | 53,532 | 349 | 398 |
| arkmon_test | 3,538 | 251 | 251 |
| **agent-report** | 3,414 | **691** | **704** |
| video-chunk | 2,366 | 295 | 296 |
| file-chunk | 1,174 | 378 | 408 |
| watson/sherlock/moriarty | ~2,400 | 280-284 | 284 |
| vote | 309 | 368 | 370 |

**Largest attribute entities**: Golem `agent-report` entities have the most string attributes (~691 bytes avg, 704 max) with fields like `node_ids`, `test_types`, `agent_tag_*`, etc.

### Example: Golem Provider Entity (18 string attrs)

```sql
SELECT key, value FROM string_attributes 
WHERE entity_key = X'00004002FE7DCDBBAE51853C260375817A8F081471608F055D52801D740B8929';
```

| Key | Value |
|-----|-------|
| `$creator` | 0x000000000000322d0bbfb94a55a9bb9ead4429d8 |
| `$key` | 0x00004002fe7dcdbbae51853c260375817a8f... |
| `$owner` | 0x000000000000322d0bbfb94a55a9bb9ead4429d8 |
| `name` | hivello |
| `provId` | 0x9700d6ff8cd36fb5d1f9fc042845ada2effcf88d |
| `efficiency` | 00000.140TH/GLM |
| `speed` | 0000.992M/s |
| `totalCost` | 0000.00906GLM |
| `totalWork` | 000001.27G |
| `totalWorkHours` | 000.35h |
| `lastJobDate` | 2025-11-23T03:30:46Z |
| ... | *(18 total string attributes)* |

---

## Payload Size Distribution

Analysis of 1,069,993 payloads in the mendoza database.

### Content Type Distribution

| Content Type | Count | % |
|--------------|-------|---|
| `application/json` | 1,012,344 | 94.6% |
| `image/png` | 52,169 | 4.9% |
| `application/octet-stream` | 3,878 | 0.4% |
| `text/plain` | 1,523 | 0.1% |
| `application/vnd.apple.mpegurl` | 50 | <0.1% |
| `image/jpeg` | 7 | <0.1% |
| `string` | 22 | <0.1% |

> Most payloads are JSON (often base64-encoded binary wrapped in JSON metadata). Raw images (`image/png`) are the second largest category.

### Overall Statistics

| Metric | Value |
|--------|-------|
| **Total Payloads** | 1,069,993 |
| **Mean Size** | 5,200 bytes (~5.1 KB) |
| **Median Size** | 144 bytes |
| **Max Size** | 240 KB |
| **Total Payload Data** | 6.2 GB |

### Size Distribution

| Bucket | Count | % of Total | Cumulative % |
|--------|-------|------------|--------------|
| **< 1 KB** | 994,713 | 93.0% | 93.0% |
| **1-5 KB** | 9,176 | 0.9% | 93.9% |
| **5-10 KB** | 5,103 | 0.5% | 94.3% |
| **10-20 KB** | 3,269 | 0.3% | 94.6% |
| **20-50 KB** | 2,350 | 0.2% | 94.8% |
| **50-100 KB** | 48,097 | 4.5% | 99.3% |
| **100-200 KB** | 7,103 | 0.7% | 100.0% |
| **200+ KB** | 182 | 0.0% | 100.0% |

![Payload Size Distribution](./data/payload_size_distribution.png)

### Key Observations

1. **Bimodal distribution**: The vast majority (93%) of payloads are tiny (<1 KB), but there's a significant secondary cluster at 50-100 KB.

2. **Small payload dominance**: Median of 144 bytes suggests most payloads are simple metadata or small data fragments.

3. **Large payload cluster**: ~55K payloads (5.2%) are in the 45-240 KB range, forming a distinct population.

### Large Payload Analysis (45 KB+)

Focused analysis on the 55,382 payloads ≥ 45 KB:

| Metric | Value |
|--------|-------|
| **Count** | 55,382 |
| **Mean** | 95 KB |
| **Median** | 96 KB |
| **Std Dev** | 19 KB |
| **Min** | 45 KB |
| **Max** | 240 KB |

#### Distribution (45 KB+)

| Bucket | Count | % of 45KB+ |
|--------|-------|------------|
| 45-50 KB | 134 | 0.2% |
| 50-55 KB | 100 | 0.2% |
| 55-60 KB | 130 | 0.2% |
| 60-65 KB | 173 | 0.3% |
| 65-70 KB | 308 | 0.6% |
| 70-75 KB | 660 | 1.2% |
| 75-80 KB | 1,776 | 3.2% |
| 80-85 KB | 4,451 | 8.0% |
| **85-90 KB** | 7,785 | 14.1% |
| **90-95 KB** | 8,105 | 14.6% |
| **95-100 KB** | 9,612 | 17.4% |
| **100-120 KB** | 15,869 | 28.7% |
| 120-150 KB | 5,876 | 10.6% |
| 150-200 KB | 221 | 0.4% |
| 200+ KB | 182 | 0.3% |

![Payload Size Distribution 45KB+](./data/payload_size_distribution_45kb_plus.png)

#### Interpretation

The large payloads form a **bell curve centered at ~95 KB** (mean=95 KB, median=96 KB). This suggests:

- A consistent entity type with structured data of predictable size
- Likely JSON/CBOR documents or serialized data structures
- The 85-120 KB range contains 73% of all large payloads
- Very few outliers beyond 150 KB

### Sample Large Payloads

Query to find large image/png payloads (Robert's cat pics 🐱):

```sql
SELECT content_type, length(payload), string_attributes, numeric_attributes 
FROM payloads 
WHERE length(payload) > 80*1024 AND content_type = "image/png" 
LIMIT 5;
```

Sample results (CCats AI-generated cat images):

| content_type | size | app | id | prompt |
|--------------|------|-----|-----|--------|
| image/png | 105,682 | CCats | 14 | "a mysterious cat, wearing ethereum pendant, ukiyo-e japanese art..." |
| image/png | 89,217 | CCats | 15 | "a fat cat, wearing LED collar, 3D render, silver and cyan..." |
| image/png | 83,806 | CCats | 16 | "a cat, wearing hacker hoodie, synthwave style..." |
| image/png | 95,631 | CCats | 17 | "a cat, wearing NFT collar, 3D render, silver and cyan..." |
| image/png | 86,445 | CCats | 1 | "a mysterious cat, anime style, red and gold, space with galaxies..." |

These AI-generated cat images (~80-110 KB each) from the CCats app contribute to the large payload cluster around 85-120 KB. The CCats images are primarily from creator `0x33f85522...5b965a` (#5 in entity ownership) with **28,191 cat images**.

---

Query to find video/mp4 chunks (mimeType is inside string_attributes JSON):

```sql
SELECT content_type, length(payload) as size, string_attributes 
FROM payloads 
WHERE length(payload) > 100*1024 AND string_attributes LIKE '%video/mp4%' 
LIMIT 5;
```

Sample results (chunked video uploads):

| content_type | size | fileName | mimeType | chunkIndex | totalChunks | type |
|--------------|------|----------|----------|------------|-------------|------|
| application/json | 175,027 | vid.mp4 | video/mp4 | 0 | 30 | file-chunk |
| application/json | 175,027 | vid.mp4 | video/mp4 | 0 | 30 | file-chunk |
| application/json | 175,027 | vid.mp4 | video/mp4 | 0 | 30 | file-chunk |

These are chunked video file uploads where each chunk is ~175 KB of base64-encoded video data wrapped in JSON. A 30-chunk video = ~5.25 MB total.

---

## Performance Metrics

| Metric | Value | Conditions |
|--------|-------|------------|
| **Row Throughput** | ~6,400 rows/sec | Full entity commits |
| **Entity Throughput** | ~700 entities/sec | With 5KB payload, 8 attrs |
| **Full Sync Time** | ~15 minutes | Complete state rebuild |
| **Key Bottleneck** | String attributes | 0.95 correlation with commit time |

---

## Scaling Multipliers

When comparing use cases to mendoza, use these as rough capacity indicators:

| Scale | Entities | Attributes | Expected Behavior |
|-------|----------|------------|-------------------|
| **0.5x mendoza** | 400K | 10M | ✅ Comfortable headroom |
| **1x mendoza** | 800K | 19M | ✅ Proven capacity |
| **2x mendoza** | 1.6M | 40M | ⚠️ Needs validation |
| **10x mendoza** | 8M | 190M | ⚠️ Requires optimization |
| **50x mendoza** | 40M | 1B | ❌ Beyond single-chain design |

---

> **Source**: See [`experiments.md`](./experiments.md) for detailed benchmark methodology and results.
