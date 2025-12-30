# Arkiv Performance Requirements

This document defines target requirements to decide about the readiness of Arkiv for canary mainnet.

Required performance metrics are derived from a use case of a decentralized market place. 
Being related to Golem, Yagna and the current collaboration with Salad are the main drivers for choosing this particular use case.

---

## Table of Contents
- [1. Context: 300K Node Compute Marketplace](#1-context-300k-node-compute-marketplace)
  - [Entity Model Overview](#entity-model-overview)
  - [Marketplace Dynamics](#marketplace-dynamics)
- [2. Write Performance Requirements](#2-write-performance-requirements)
  - [2.1 Node Entity Updates](#21-node-entity-updates)
  - [2.2 Workload Entity Creations/Updates](#22-workload-entity-creationsupdates)
  - [2.3 Combined Write Load](#23-combined-write-load)
  - [2.4 Write Performance Design Target](#24-write-performance-design-target)
- [3. Read Performance Requirements](#3-read-performance-requirements)
  - [3.1 Problem Statement](#31-problem-statement)
  - [3.2 Design Goals](#32-design-goals)
  - [3.3 Resource-Driven Age-Based Bucket Strategy](#33-resource-driven-age-based-bucket-strategy)
  - [3.4 Query Estimates](#34-query-estimates)
- [4. Combined Read/Write Requirements](#4-combined-readwrite-requirements)
  - [4.1 Load Scenarios](#41-load-scenarios)
  - [4.2 Design Targets](#42-design-targets)
  - [4.3 Requirements Summary](#43-requirements-summary)
- [5. Validation and Tuning](#5-validation-and-tuning)
  - [Validation](#validation)
  - [Options for Tuning](#options-for-tuning)

---
# 1. Context: 300K Node Compute Marketplace

A realistic performance model for a 300K node decentralized compute marketplace with workload scheduling.

300k nodes seems to correspond to mid-large sized hyper scaler data centers, although it is difficult to find direct sources for these numbers. 

## Marketplace Dynamics

The **balanced marketplace model** assumes supply and demand in reasonable equilibrium:
- 300K total nodes: 200K busy + 90K available + 10K offline (66% utilization)
- 100K pending queue = **2.5 hour wait time** (healthy marketplace)
- Dynamic pricing adjusts to maintain this balance

**Steady State Flow:**
- 200K busy nodes with 5-hour average workload duration → **40K workloads complete per hour**
- 40K new workloads submitted per hour (to maintain steady state)
- Matcher assigns 40K pending workloads to newly freed nodes per hour
- Net effect: Queue size and utilization remain stable at 100K pending and 66% busy

**Why 100K nodes might remain available (33% idle capacity):**
- **Matching friction**: Heterogeneous resources (GPU types, regions, VM specs) prevent perfect matching - available CPU nodes can't run GPU-requiring workloads
- **Matching latency**: Matcher runs in 10-second cycles with batch limits (see [3.3 Resource-Driven Age-Based Bucket Strategy](#33-resource-driven-age-based-bucket-strategy)), creating brief idle periods as nodes wait for assignment
- **Economic equilibrium**: Dynamic pricing keeps some capacity idle as buffer for burst demand and premium placements
- **Queue discipline**: FIFO fairness means older workloads get priority, even if newer workloads better match recently freed nodes

This 66% utilization is somewhat higher than typical cloud VMs (40-60%).

Alternative scenarios show why this matters:
- **Demand-heavy** (eg GPU shortage): 400K pending, 20h wait → aggressive matching needed
- **Supply-heavy** (excess capacity): 5K pending, <30min wait → nodes sit idle, prices drop

The balanced model represents a mature and sustainable marketplace with reasonable queue depths and good resource utilization.
## Entity Model Overview

The marketplace manages two primary entity types:

**Nodes** (physical compute resources):
- Status attributes: `status` (available/busy/offline), `assigned_workload_id`
- Resource attributes: `cpu_cores`, `ram_gb`, `gpu_count`, `disk_gb`
- Health metrics: `cpu_util`, `mem_util`, `disk_util` (conditional updates on >10% change)
- Location/capability: `region`, `vm_type`, `gpu_type`, `os_version`
- Pricing: `price_per_hour`

**Workloads** (jobs to be executed):
- Status attributes: `status` (pending/running/completed/failed), `assigned_node_id`
- Resource requirements: `req_cpu_cores`, `req_mem_gb`, `req_gpu_count`
- Resource usage: `actual_cpu`, `actual_mem` (conditional updates on >20% change)
- Constraints: `required_region`, `required_gpu_type`, `max_price`
- Lifecycle: `submitted_at`, `started_at`, `completed_at`, `retry_count`

**Supporting entities** include racks, switches, PDUs, cooling systems, workload templates, placement groups, tenants, and alert rules (~25K infrastructure + management entities).

**Total system**: ~663K entities, ~9.3M attributes (average of 14 attributes per entity)

# 2. Write Performance Requirements

## 2.1 Node Entity Updates

**Model Parameters:** See [Marketplace Dynamics](#marketplace-dynamics) for system overview.
- 300K nodes (physical capacity)
- 300K total workloads in system (steady state with housekeeping)
- 200K workloads running at any time (66% node utilization)
- 100K workloads pending (waiting for nodes)
- Average workload duration: 5 hours
- **Workload throughput: 200K running / 5h = 40K/hour = 11.1 workloads/sec**

**Node Status Update Drivers:**

```
Workload assignments:  11.1 nodes/sec (available → busy)
Workload completions:  11.1 nodes/sec (busy → available)
Hardware failures:     1.5 nodes/sec (any → offline)
Maintenance returns:   1.5 nodes/sec (offline → available)
                       ────────────────────────────────────
Entity status changes:   ~25 nodes/sec
```

**Other Node Updates:**

| Update Type | Frequency | Trigger | Entities/Update | Notes |
|-------------|-----------|---------|-----------------|-------|
| **Status Changes** | Continuous | Workload lifecycle + failures | ~25 nodes/sec | Driven by 11.1 workloads/sec throughput |
| **Health Metrics** | Conditional | Metrics change >10% | ~30-45 nodes/sec | Event-driven: cpu_util, mem_util, disk_util |
| **Config Updates** | Hourly-Daily | Software updates, patches | 300-15000 nodes | Rolling updates, staged deployments |
| **Hardware Changes** | Weekly-Monthly | Physical maintenance | 30-300 nodes | Rack moves, upgrades, decommissions |

**Health Metrics Strategy:** Each node checks its health every minute (CPU, memory, disk utilization). Updates are only written when metrics change substantially (>10 percentage points) or cross critical thresholds (>80%, >90%). This event-driven approach captures important state changes while avoiding redundant writes for stable nodes. 

Most nodes (70%) have stable workloads and write infrequently, while active or problematic nodes write more often, providing real-time visibility where it matters.

**Write Load Calculation:**

```
Status changes:        25 nodes/sec (includes matching: available→busy, busy→available)
Health metrics:        37 nodes/sec (conditional writes on substantial change, using midpoint)
Config updates:        1,500 nodes / 3600 sec = 0.42 nodes/sec
                       ───────────────────────────────────
Total steady-state:    ~62 node entities/sec
```

**Peak scenarios:**
- **Mass node failure**: 3,000 nodes in <10 sec = **300 nodes/sec spike** (status + health)
- **Rolling restart**: 30K nodes/hour = **8 nodes/sec** for 1 hour

## 2.2 Workload Entity Creations/Updates

**Operational Dynamics:**

| Update Type | Average (steady-state) | Trigger | Entities/Operation | Notes |
|-------------|-----------|---------|-------------------|-------|
| **New Workloads** | 11.1 workloads/sec | User submissions | 11.1 workloads/sec | Bursty (10-80 range during peaks/troughs) |
| **Workload Scheduling** | 11.1 workloads/sec | Matcher assigns to node | 11.1 workloads/sec | status: pending→running, assigned_node |
| **Workload Completion** | 11.1 workloads/sec | Job finishes | 11.1 workloads/sec | status: running→completed, resource release |
| **Workload Failures** | 3 workloads/sec | Timeout, OOM, crashes | 3 workloads/sec | status: running→failed, retry_count++ |
| **Resource Updates** | 40 workloads/sec | Usage change >20% | 40 workloads/sec | Event-driven: actual_cpu, actual_mem |

**Resource Update Assumptions:** Workload mix of 60% mostly stable compute jobs (20 updates/s), 30% workloads with moderate variability (60 updates/s) and 10% workloads with high variability (100 updates/s).

**Workload Lifecycle:**

```
pending (new) → running (scheduled) → completed/failed (done)
```

**Write Load Calculation:**

```
New submissions:       11.1 workloads/sec (create)
Scheduling (assign):   11.1 workloads/sec (update)
Completions:           11.1 workloads/sec (update)
Failures:              3 workloads/sec (update)
Housekeeping:          11.1 workloads/sec (cleanup completed for steady-state)
Resource updates:      40 workloads/sec (conditional: usage change >20%)
                       ─────────────────────────────────────────
Total steady-state:    ~87 workload entities/sec
```

**Resource Update Strategy:** Similar to health metrics, workloads check their actual resource usage every minute. Updates are only written when usage changes substantially (>20% deviation from last reported values). This captures meaningful billing/SLA events while avoiding redundant writes for stable workloads.

**Peak scenarios:**
- **Batch job submission**: Constrained by block capacity (2-sec blocks) = **~200 workloads/sec** (create)
- **Mass completion** (batch done): 3,000 workloads / 10 sec = **300 workloads/sec** (update)
- **Resource usage spike**: Many workloads change usage simultaneously = **~450 workloads/sec** (brief, creates temporary backlog)

## 2.3 Combined Write Load

| Scenario | Node Updates | Workload Updates | Total | Peak Duration |
|----------|--------------|------------------|-------|---------------|
| **Steady-state** | ~62 entities/sec | ~87 entities/sec | **~149 entities/sec** | Continuous |
| **Batch submission** | ~62 entities/sec | 200 entities/sec | **~262 entities/sec** | Block rate limited |
| **Resource usage spike** | ~62 entities/sec | 450 entities/sec | **~512 entities/sec** | 30-60 sec, occasional |
| **Mass node failure** | 300 entities/sec | ~87 entities/sec | **~387 entities/sec** | <10 sec, rare |
| **Mass job completion** | ~62 entities/sec | 300 entities/sec | **~362 entities/sec** | ~10 sec, occasional |

## 2.4 Write Performance Design Target

**Target Rates:**
- **Sustained**: 250 entities/sec (68% headroom over 149/sec steady-state, accommodates normal bursts like batch submissions at 262/sec)
- **Peak**: 500 entities/sec (matches block capacity: 1,000 entities / 2-sec blocks)
- **Batch size**: 500-1,000 entity updates per transaction

**Block Constraint Impact:** Peak scenarios at or near 500 entities/sec will create temporary backlogs that clear once the spike ends. The system processes at block capacity (500/sec), so a 30-second spike generates exactly 30 seconds of processing time—no accumulating backlog.

# 3. Read Performance Requirements

## 3.1 Problem Statement

**Core Challenge:** Match 100K pending workloads to 90K available nodes efficiently in a 300K node compute marketplace.

**Key Constraints:**
- **Matching pool**: 90K available nodes (200K busy, 10K offline)
- **Workload throughput**: 11.1 workloads/sec = 40K/hour
- **Fairness requirement**: Oldest workloads must be prioritized
- **Resource efficiency**: Idle nodes should not remain unused while workloads wait

## 3.2 Design Goals

1. **Minimize query count**: Avoid per-workload queries (11.1 queries/sec), use batch approach
2. **Avoid ORDER BY where possible**: Use filters with price ceilings instead of sorting large result sets
3. **Maximize resource utilization**: Continue matching from younger age buckets while nodes available
4. **Query performance**: Keep individual queries fast with LIMIT constraints
5. **Fair queuing**: Process workloads by age priority (oldest first)

## 3.3 Resource-Driven Age-Based Bucket Strategy

**Approach:** Organize the pending workloads into 10 age buckets (2-hour increments), process oldest first, continue through younger buckets while nodes remain available.

**Age Buckets:**
- Bucket 0: 0-2 hours (newest)
- Bucket 1: 2-4 hours
- Bucket 2: 4-6 hours
- ...
- Bucket 9: 24+ hours (oldest, highest priority)

**Matching Algorithm (Every 10-second cycle):**

1. **Process buckets oldest-first**:
   - **Step 1: Fetch workload batch** from bucket 9 (24h+), LIMIT 100
   - **Step 2: Analyze workload constraints** from batch:
     - Unique regions needed (e.g., us-east, eu-west)
     - Unique vm_types needed (e.g., gpu, cpu-only)
     - Resource ranges (min/max CPU, RAM, GPU)
     - Price ceiling (max budget across batch)
   - **Step 3: Fetch matching nodes** using aggregated constraints, eg LIMIT 150:
     - Filter by regions IN (discovered regions)
     - Filter by vm_types IN (discovered types)
     - Filter by cpu_cores >= min_needed, ram_gb >= min_needed
     - Filter by price_per_hour <= max_budget
     - Result: Fetch only nodes that could match this batch (assumption: 1K-5K available nodes for these criteria)
   - **Step 4: Match in-memory**: Pair workloads to nodes
   - **Step 5: Update Entities** Batch update matched workloads/nodes
   - **Step 6: try to exhaust current workload bucket**: Go back to Step 1 as long as there are unmatched workloads and available nodes.
2. **Move to next bucket**: While not working on youngest bucket

**Key Features:**
- **Age-based priority**: Always starts with oldest bucket
- **Data-driven node queries**: Only fetch nodes matching current workload batch requirements
- **Per-query limits**: 100 workloads per query, only relevant nodes fetched
- **High number of matches per cycle**: Can match hundreds/thousands of workloads if nodes available. Matches only limited by available time window.
- **Fair queuing**: Older workloads never starve

## 3.4 Query Estimates

**Per Cycle (10 seconds):**

- Matches per cycle: 0-60K (during peak bursts, many matches will be buffered in the Arkiv node's transaction mempool due to block size limits of ~1,000 entities per 2-second block)
- Workloads per query: 100, fixed LIMIT for consistent performance
- Nodes per query: 150, limit at 1.5x workload limit

| Metric | Value | Notes |
|--------|-------|-------|
| **Workload queries** | 1-100 per cycle | One per batch; Step 6 loops exhaust buckets before moving on |
| **Node queries** | 1-100 per cycle | One per workload batch (paired with workload query) |
| **Total queries** | 2-200 per cycle | Workload + Node query pairs |
| **Average query rate** | 0.2-20 queries/sec | 2-200 queries / 10-second cycle |

**Total Read Operations:**

| Query Type | Frequency (per sec) | Purpose |
|------------|---------------------|---------|
| **Matching strategy** | 0.2-20 | Age-bucket workload queries and node queries |
| **Point lookups** | 40-100 | Node/workload details by ID (API endpoints, monitoring) |
| **Health alerts** | 5-20 | Query recent alert entities for dashboard/notifications |
| **Tenant accounting** | 40 | Quota checks, billing |
| **Dashboard/reporting** | 20-60 | UI queries, analytics (cached, periodic refresh) |
| **Total steady-state** | **~105-240 queries/sec** | Normal operations |

**Monitoring Strategy:** Health monitoring is fully **event-driven**. Nodes publish health metric updates only when substantial changes occur (>10% deviation). For problematic conditions (CPU >90%, memory exhaustion, disk full), nodes create dedicated **health alert entities** that can be efficiently queried via indexed timestamps. The monitoring system queries recent alerts rather than sweeping the entire fleet, making monitoring load proportional to problems, not fleet size.

**Peak Scenarios:**
- **Scheduler burst**: 10x matching rate = **200 queries/sec** (matching queries during high workload submission)
- **Dashboard refresh spike**: Many users checking status simultaneously = **500 queries/sec** (brief)
- **Design target**: **500 queries/sec sustained** | Sufficient for realistic load with headroom

# 4. Combined Read/Write Requirements

## 4.1 Load Scenarios

| Scenario | Write (entities/sec) | Read (queries/sec) | Notes |
|----------|---------------------|-------------------|-------|
| **Steady-state** | 149 | 105-240 | Balanced marketplace, event-driven monitoring |
| **Batch submission** | 262 | 200 | Block rate limited (2-sec blocks) |
| **Resource usage spike** | 512 | 105-240 | At block capacity, creates temporary backlog |
| **Mass node failure** | 387 | 105-240 | Node failures + normal workload operations |
| **Mass job completion** | 362 | 200 | Large batch finishes |
| **Dashboard spike** | 149 | 500 | Many users checking status |

## 4.2 Design Targets

Based on the scenarios above, the performance targets for Arkiv v1 are:

**Write Performance:**
- **Sustained target**: **500 entities/sec**
  - Rationale: Resource usage spikes (512/sec) represent the highest realistic sustained load, occurring when many workloads simultaneously change resource consumption. This matches exactly the block capacity constraint (1,000 entities per 2-sec block).
  - Margin: Steady-state (149/sec) runs at 30% of capacity, providing substantial headroom for normal operations.

**Read Performance:**
- **Sustained target**: **500 queries/sec**
  - Rationale: Dashboard spikes (500 qps) represent the highest realistic read load, occurring when many users simultaneously check marketplace status during peak hours or after incidents.
  - Margin: Steady-state (105-240 qps) runs at 21-48% of capacity, with most operational scenarios (batch submission, job completion) at 200 qps (40% of capacity).

**Design Philosophy:**
- Targets are **block-constrained** (500 writes/sec = 1,000 entities / 2-sec blocks)
- Targets are **event-driven** (monitoring scales with activity, not fleet size)
- Targets provide **realistic headroom** (peaks are brief and recoverable, not sustained)

## 4.3 Requirements Summary

**Write Requirements:**

| Metric | Target | Notes |
|--------|--------|-------|
| **Sustained throughput** | 250 entities/sec | Steady-state with headroom |
| **Peak throughput** | 500 entities/sec | Block capacity (1,000 entities / 2-sec blocks) |
| **Transaction size** | 500-1,000 entities | Batch for efficiency |
| **Commit latency (p99)** | <1500ms | 75% of block time (2s), allows 500ms buffer for additional latency, etc. while keeping 99% of writes within single block |
| **Write availability** | 99.7% | 24h/year downtime acceptable per node (OP Stack multi-node redundancy reduces overall network downtime) |

**Read Requirements:**

| Metric | Target | Notes |
|--------|--------|-------|
| **Sustained throughput** | 250 queries/sec | Steady-state with headroom |
| **Peak throughput** | 500 queries/sec | Dashboard/monitoring spikes |
| **Query latency (p50)** | <10ms | Mixed query load: point lookups by entity_id (<10ms) and simple attribute queries dominate; EAV design overhead minimal for common cases |
| **Query latency (p99)** | <200ms | Complex multi-attribute matching queries with EAV + bi-temporal joins; allows 1% outliers during high load |
| **Result set size** | 1-100 rows typical | Application-side final selection |
| **Read availability** | 99.7% | 24h/year downtime acceptable per node (OP Stack multi-node redundancy reduces overall network downtime) |

# 5. Validation and Tuning

## Validation

To validate that Arkiv meets these requirements:

1. **Batch write testing**: Verify 500 entities/sec sustained throughput
2. **Latency under load**: Confirm p99 commit latency <1500ms at sustained write rates
3. **Query performance**: Verify matching queries complete in <100ms with realistic data volumes
4. **Concurrent operations**: Test simultaneous reads/writes at target loads (250 write + 250 read sustained)

## Options for Tuning

1. **SQL query optimization**
2. **Lowering stated requirements**: Keep current features set and motivate lower requirements
3. **Drop deterministic result sets for paging**: Remove bi-temporal data handling
4. **Question choice of SQLite**: Substantial impact on efforts and time scale likely

---
