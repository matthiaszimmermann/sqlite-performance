# Arkiv Use Cases

This document describes realistic use cases for arkiv entity management, based on the SQLite performance findings from our experiments.

---

## Table of Contents

- [Use Case Overview](#use-case-overview)
  - [Candidate Use Cases](#candidate-use-cases)
  - [Comparison Matrix](#comparison-matrix)
  - [Selected Use Cases for Deep Dive](#selected-use-cases-for-deep-dive)
- [Use Case 1: Decentralized Compute Marketplace](#use-case-1-decentralized-compute-marketplace)
- [Use Case 2: Decentralized Ride-Sharing](#use-case-2-decentralized-ride-sharing)
- [Use Case 3: Decentralized Freelance/Gig Platform](#use-case-3-decentralized-freelancegig-platform)
- [Use Case 4: Decentralized Supply Chain Tracking](#use-case-4-decentralized-supply-chain-tracking)
- [Use Case 5: Decentralized Energy Grid (P2P Trading)](#use-case-5-decentralized-energy-grid-p2p-trading)
- [Use Case 6: Decentralized Carbon Credits](#use-case-6-decentralized-carbon-credits)
- [Mendoza Benchmark Reference](#mendoza-benchmark-reference)
- [Mendoza vs Use Case Alignment](#mendoza-vs-use-case-alignment)
- [Requirements Summary](#requirements-summary)
  - [SQLite Performance Characteristics](#sqlite-performance-characteristics)
  - [Comparison Across Use Cases](#comparison-across-use-cases)
  - [Use Case Fit Assessment](#use-case-fit-assessment)
  - [Recommended Priority Order](#recommended-priority-order)
- [Compute Marketplace Performance Requirements Framework](#compute-marketplace-performance-requirements-framework)
  - [Context: 300K Node Compute Marketplace](#context-300k-node-compute-marketplace)
  - [Write Performance Requirements](#write-performance-requirements)
    - [Node Entity Updates](#node-entity-updates)
    - [Workload Entity Creations/Updates](#workload-entity-creationsupdates)
    - [Combined Write Load](#combined-write-load)
  - [Read Performance Requirements](#read-performance-requirements)
    - [Problem Statement](#problem-statement)
    - [Design Goals](#design-goals)
    - [Resource-Driven Age-Based Bucket Strategy](#resource-driven-age-based-bucket-strategy)
    - [Query Estimates](#query-estimates)
    - [Combined Read/Write Load](#combined-readwrite-load)
  - [Performance Framework Summary](#performance-framework-summary)
  - [Next Steps for Validation](#next-steps-for-validation)
- [Performance Assumptions](#performance-assumptions)

---

## Use Case Overview

Before diving into specific scenarios, here's a comparative analysis of potential arkiv use cases across multiple dimensions.

### Candidate Use Cases

| Category | Use Case | Description |
|----------|----------|-------------|
| **Infrastructure** | Compute Marketplace | Decentralized compute resource management |
| **Infrastructure** | Energy Grid | Peer-to-peer energy trading and grid management |
| **Mobility** | Ride-Sharing | Decentralized alternative to Uber/Lyft |
| **Mobility** | Fleet Management | Logistics and delivery coordination |
| **Commerce** | Freelance/Gig Platform | Worker-owned alternative to Upwork/Fiverr |
| **Commerce** | Property Registry | Land and property ownership records |
| **Supply Chain** | Supply Chain Tracking | Multi-party provenance and traceability |
| **Supply Chain** | Carbon Credits | Transparent offset tracking and trading |
| **Identity** | Health Records | Patient-controlled medical data |
| **Identity** | Professional Credentials | Verifiable diplomas and licenses |
| **Governance** | Voting/Governance | DAO and organizational decision-making |
| **Governance** | Cooperative Management | Member-owned organization coordination |

### Comparison Matrix

| Use Case | Entities | Update Frequency | Decentralization Value | Arkiv Fit | Real-World Traction |
|----------|----------|------------------|------------------------|-----------|---------------------|
| **Compute Marketplace** | 600K | Periodic | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **Ride-Sharing** | 50M+ | Real-time | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ |
| **Supply Chain** | 10M | Checkpoint | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Fleet Management** | 1M | Minutes | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **Health Records** | 50M | Event | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ |
| **Credentials** | 100M | Rare | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| **Freelance Platform** | 50M | Lifecycle | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **Property Registry** | 100M | Rare | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| **Energy Grid** | 10M | 15 min | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **Carbon Credits** | 1B credits | Event | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **Voting/Governance** | 100M | Burst | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| **Cooperative Mgmt** | 1M | Event | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |

### Evaluation Criteria

**Decentralization Value**: Why does this need to be decentralized?
- ⭐⭐⭐⭐⭐ Multiple untrusting parties, platform exploitation, or censorship concerns
- ⭐⭐⭐ Some benefit but single-entity management also viable
- ⭐ Traditional centralized approach works fine

**Arkiv Fit**: How well does arkiv's entity model and throughput match?
- ⭐⭐⭐⭐⭐ Entity counts <10M, checkpoint/event updates, clear entity structure
- ⭐⭐⭐ Moderate scale or update frequency challenges
- ⭐ Real-time requirements or massive scale beyond current benchmarks

**Real-World Traction**: Are there existing projects or clear market demand?
- ⭐⭐⭐⭐⭐ Active projects, proven market need
- ⭐⭐⭐ Pilot projects, emerging interest
- ⭐ Theoretical interest only

### Selected Use Cases for Deep Dive

Based on strategic fit, technical feasibility, and real-world relevance, we explore these six use cases in detail:

| # | Use Case | Rationale |
|---|----------|-----------|
| 1 | **Compute Marketplace** | Historical context (Golem), ongoing partner collaboration |
| 2 | **Ride-Sharing** | Strong decentralization story, tests arkiv at scale limits |
| 3 | **Freelance/Gig Platform** | Worker ownership narrative, manageable update patterns |
| 4 | **Supply Chain Tracking** | Excellent multi-party fit, proven enterprise demand |
| 5 | **Energy Grid P2P** | Growing market, update patterns match arkiv well |
| 6 | **Carbon Credits** | High visibility, clear transparency needs |

---

## Use Case 1: Decentralized Compute Marketplace

### Context: Hyperscaler Compute Landscape

Major cloud providers operate extensive global infrastructure:

| Company | Compute Facilities | Regions |
|---------|--------------|---------|
| Amazon (AWS) | ~100+ | 33 regions, 105 AZs |
| Microsoft Azure | ~60+ | 60+ regions |
| Google Cloud | ~40+ | 40 regions, 121 zones |
| Meta | ~20+ | N/A (private) |
| Alibaba Cloud | ~25+ | 29 regions |

### The Compute Concentration Problem

The cloud market is dangerously concentrated:

| Metric | Value | Trend |
|--------|-------|-------|
| Top 3 cloud market share | ~65% | Consolidating |
| AI/GPU compute concentration | Even higher | Worsening |
| 2024 GPU availability crisis | Months-long waitlists | Recent memory |

This concentration creates real problems:

| Problem | Impact | Decentralized Solution |
|---------|--------|------------------------|
| **Hyperscaler monopoly** | Pricing power, limited choice | Competitive marketplace of providers |
| **GPU scarcity** | AI boom made compute unobtainable | Distributed GPU marketplace |
| **Vendor lock-in** | Hard to leave AWS once committed | Portable workloads, multi-provider |
| **Censorship/deplatforming** | Parler/AWS showed cloud as gatekeeper | Censorship-resistant compute |
| **Data sovereignty** | Your data on their servers, their rules | Choose your jurisdiction/provider |
| **Single points of failure** | us-east-1 outages take down half the internet | Geographic + provider diversity |
| **Pricing opacity** | Complex, unpredictable bills | Transparent marketplace pricing |

### Existing Decentralized Compute Projects

| Project | Model | Scale | Status |
|---------|-------|-------|--------|
| **Golem** | P2P compute marketplace | ~10K providers | Active |
| **Akash** | Decentralized cloud | ~20K active leases | Growing |
| **Flux** | Decentralized infrastructure | ~15K nodes | Active |
| **Render** | Distributed GPU rendering | Growing | Active |

These prove the model works and demand exists.

### Why Arkiv Fits Perfectly

Decentralized compute requires coordination across untrusting parties:

| Coordination Need | Arkiv Capability |
|-------------------|------------------|
| **Node registry** | Entity management for providers |
| **Workload matching** | Attribute queries for scheduling |
| **Reputation tracking** | Immutable history of provider reliability |
| **Resource accounting** | Usage, payments, SLA compliance |
| **Multi-party state** | Providers, users, and protocol agree on truth |

### Resilience Value

| Scenario | Centralized (AWS) | Decentralized Network |
|----------|-------------------|----------------------|
| Region outage | Major disruption | Workloads auto-migrate |
| Provider bankruptcy | Catastrophic | Others absorb capacity |
| Government seizure | Single point of attack | Distributed risk |
| Pricing spike | Take it or leave | Market competition |
| Capacity crunch | Wait for hyperscaler | Market finds supply |

### Scale Context

Each large facility typically houses **100,000–500,000 servers**. The 300K node scenario modeled here represents a **large-scale balanced compute marketplace network**—the kind of scale that would emerge in a mature decentralized compute ecosystem where supply and demand are in reasonable equilibrium.

Based on our SQLite performance experiments, **a single arkiv database chain is more than sufficient to manage a large compute marketplace**. With ~663K entities and ~15.3M attributes, this scenario requires only 80-85% of the capacity demonstrated with the mendoza benchmark data. A full state sync completes in ~20 minutes, and steady-state updates of 170-235 entities/sec (with peaks up to 1,600 entities/sec) are well within arkiv's throughput capabilities.

In a decentralized architecture, each provider network maintains its own local arkiv chain for node and workload state, while participating in a broader coordination layer for cross-network visibility.

### Overview

A decentralized compute network managing 300K nodes and 300K workloads across multiple providers, enabling censorship-resistant, resilient workload distribution.

### Infrastructure Entities

| Entity Type | Count | Description |
|-------------|-------|-------------|
| **Nodes (Servers)** | 100,000 | Physical/virtual compute nodes |
| **Racks** | 2,500 | ~40 nodes per rack |
| **Rows** | 250 | ~10 racks per row |
| **Rooms/Halls** | 25 | ~10 rows per room |
| **Network Switches** | 5,000 | ToR (2 per rack) + aggregation + spine |
| **Power Distribution Units (PDUs)** | 5,000 | ~2 per rack |
| **Cooling Units (CRACs/CRAHs)** | 100 | Per room/zone |

### Per-Node Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | hostname, serial_number, asset_tag, uuid | string |
| **Location** | rack_id, row_id, room_id, position_in_rack | string/numeric |
| **Hardware** | cpu_model, cpu_cores, ram_gb, disk_tb, gpu_count | string/numeric |
| **State** | status (available/working/down/maintenance), health_score | string/numeric |
| **Config** | os_version, kernel_version, config_hash, last_config_update | string |
| **Current Load** | cpu_util_pct, mem_util_pct, disk_util_pct, network_mbps | numeric |
| **Scheduling** | labels, taints, allocatable_cpu, allocatable_mem_gb, allocatable_gpu | string/numeric |
| **Workload** | running_jobs, assigned_tenant, workload_class, assigned_workloads | string/numeric |
| **Projections** | projected_cpu_1h, projected_mem_1h, available_capacity_pct | numeric |

### Workload Entities

| Entity Type | Count | Description |
|-------------|-------|-------------|
| **Workloads (Jobs/Services)** | 500,000 | Active workloads to schedule/manage |
| **Workload Templates** | 1,000 | Reusable workload definitions |
| **Placement Groups** | 5,000 | Affinity/anti-affinity groupings |
| **Resource Quotas** | 500 | Per-tenant resource limits |

### Per-Workload Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | workload_id, name, owner_tenant, created_at | string |
| **State** | status (pending/running/completed/failed), assigned_node, start_time | string |
| **Resource Requests** | req_cpu_cores, req_mem_gb, req_disk_gb, req_gpu_count, req_network_mbps | numeric |
| **Resource Limits** | limit_cpu_cores, limit_mem_gb, limit_disk_gb | numeric |
| **Scheduling** | priority, preemptible, max_runtime_sec, retry_count | numeric |
| **Affinity** | placement_group_id, affinity_rules, anti_affinity_rules | string |
| **Constraints** | required_gpu_type, required_cpu_arch, required_os, required_zone | string |
| **Metadata** | template_id, labels, annotations | string |

### Supporting Entities

#### Management Entities

| Entity Type | Count | Purpose |
|-------------|-------|---------|
| Workload Classes | 50 | Categories of jobs/services |
| Tenants | 500 | Teams/customers |
| Maintenance Windows | 100 | Scheduled maintenance |
| Alert Rules | 1,000 | Monitoring thresholds |

#### Placement Groups (Affinity/Anti-Affinity)

| Attribute | Type | Description |
|-----------|------|-------------|
| group_id | string | Unique identifier |
| group_type | string | affinity / anti-affinity / spread |
| scope | string | rack / row / room / zone |
| member_workloads | string | List of workload IDs (or computed) |
| max_members_per_scope | numeric | For spread groups |

#### Resource Quotas (Per-Tenant Limits)

| Attribute | Type | Description |
|-----------|------|-------------|
| tenant_id | string | Tenant identifier |
| max_cpu_cores | numeric | Total CPU limit |
| max_mem_gb | numeric | Total memory limit |
| max_gpu_count | numeric | Total GPU limit |
| max_workloads | numeric | Concurrent workload limit |
| used_cpu_cores | numeric | Current usage |
| used_mem_gb | numeric | Current usage |
| used_gpu_count | numeric | Current usage |
| used_workloads | numeric | Current count |

### Entity Count Summary

| Entity Type | Count | String Attrs | Numeric Attrs | Total Attrs |
|-------------|-------|--------------|---------------|-------------|
| **Infrastructure** | | | | |
| Nodes | 300,000 | 12 | 15 | 8,100,000 |
| Racks | 7,500 | 5 | 4 | 67,500 |
| Rows | 750 | 4 | 3 | 5,250 |
| Rooms | 75 | 5 | 4 | 675 |
| Switches | 15,000 | 6 | 5 | 165,000 |
| PDUs | 15,000 | 4 | 6 | 150,000 |
| Cooling | 300 | 3 | 5 | 2,400 |
| **Workloads** | | | | |
| Workloads | 300,000 | 12 | 10 | 6,600,000 |
| Workload Templates | 3,000 | 10 | 8 | 54,000 |
| Placement Groups | 15,000 | 5 | 2 | 105,000 |
| Resource Quotas | 1,500 | 2 | 12 | 21,000 |
| **Management** | | | | |
| Workload Classes | 150 | 4 | 2 | 900 |
| Tenants | 1,500 | 5 | 3 | 12,000 |
| Maintenance Windows | 300 | 4 | 3 | 2,100 |
| Alert Rules | 3,000 | 6 | 4 | 30,000 |
| **Total** | **~663,000** | | | **~15.3M** |

### Grand Total

| Metric | Value |
|--------|-------|
| **Entities** | ~663,000 |
| **Total Attributes** | ~15.3 million |
| **Payloads** | ~345,000 (config blobs, ~5KB avg) |

### Comparison to Mendoza Benchmark Data

| Metric | Mendoza | Decentralized DC | Ratio |
|--------|---------|------------------|-------|
| Entities | 800K | 663K | 0.83x |
| String attrs | 12M | ~7.6M | 0.63x |
| Numeric attrs | 7.3M | ~6.3M | 0.86x |
| Total attrs | 19.4M | ~15.3M | 0.79x |

**Conclusion**: A decentralized compute marketplace with 300K nodes and 300K workloads would require roughly **80-85% of the mendoza dataset size** — within SQLite's comfortable range based on our benchmarks.

### Scaling Projections

| Scale | Nodes | Workloads | Total Entities | Total Attrs | Full Sync |
|-------|-------|-----------|----------------|-------------|-----------|
| Small | 10K | 50K | ~62K | ~1.4M | ~1.5 min |
| Medium | 300K | 300K | ~663K | ~15.3M | ~20 min |
| Large | 500K | 2.5M | ~3.1M | ~70M | ~74 min |
| XL | 1M | 5M | ~6.2M | ~140M | ~148 min |

### Workload Assignment Query Pattern

To determine if workload W can be assigned to node N, the system checks:

```
1. Check resource availability:
   N.allocatable_cpu >= W.req_cpu_cores
   N.allocatable_mem_gb >= W.req_mem_gb
   N.allocatable_gpu >= W.req_gpu_count

2. Check hardware constraints:
   W.required_gpu_type IN N.labels
   W.required_cpu_arch == N.cpu_arch
   W.required_os == N.os_version

3. Check affinity rules:
   IF W.placement_group.type == 'affinity':
     N hosts other members of W.placement_group
   IF W.placement_group.type == 'anti-affinity':
     N does NOT host other members of W.placement_group

4. Check tenant constraints:
   IF N.taints contains 'dedicated=tenant-X':
     W.owner_tenant == 'tenant-X'

5. Check quota:
   W.owner_tenant.used_cpu + W.req_cpu <= quota.max_cpu
```

### Update Frequency Considerations

| Operation | Frequency | Entities Affected |
|-----------|-----------|-------------------|
| Workload creation | 100-1000/sec | 1 workload + quota update |
| Workload completion | 100-1000/sec | 1 workload + node + quota |
| Node metrics update | Every 10-60 sec | 100K nodes |
| Workload status update | Every 1-10 sec | Active workloads only |

For a 100K node / 500K workload system with typical update patterns:
- **Steady state updates**: ~10K-50K attribute updates/sec
- **Burst (rebalancing)**: ~100K-500K attribute updates/sec

Based on our benchmarks (~6,400 rows/sec), this is achievable for steady state but may require batching or optimization for burst scenarios.

### Arkiv Requirements: Compute Marketplace

| Requirement | Value | Notes |
|-------------|-------|-------|
| **Entities** | 663K | Per provider network |
| **Attributes** | 13.9M | ~22 attrs/entity average |
| **Writes/sec** | 10K-50K | Steady state; burst to 500K during rebalancing |
| **Reads/sec** | 1K-10K | Workload matching, node queries |
| **Chains needed** | 1 per DC | 50-100 for hyperscaler-scale network |
| **DB size estimate** | ~10 GB | 0.72x mendoza attrs, fewer payloads |
| **Sync time** | ~15 min | Full state sync |

---

## Use Case 2: Decentralized Ride-Sharing

### Context: Global Ride-Sharing Scale

Major ride-sharing platforms operate at massive global scale:

| Metric | Count | Notes |
|--------|-------|-------|
| **Active drivers globally** | ~5-6 million | Monthly active |
| **Registered users globally** | ~150-160 million | Unique accounts |
| **Daily trips** | ~25 million | Rides + food delivery |
| **Peak concurrent rides** | ~1-2 million | During rush hours |
| **Cities served** | ~10,000+ | Across 70+ countries |

### Regional vs. Global Entities

A key insight for decentralized architecture: **drivers are regional, users are global**.

| Entity Type | Scope | Reasoning |
|-------------|-------|-----------|
| **Drivers** | Regional/City | 99%+ operate in a single metro area |
| **Vehicles** | Regional/City | Tied to driver location |
| **Active Rides** | City | Real-time, local matching |
| **Surge Zones** | City | Hyper-local demand signals |
| **Users** | Global identity, regional activity | ~15-20% travel across regions |

This suggests a **tiered architecture** where a single arkiv chain manages a city or metro area.

### Tier 1: City/Metro Arkiv Chain

Each major city runs its own arkiv chain for real-time operations.

#### City-Level Entities

| Entity Type | Count (Large City) | Description |
|-------------|-------------------|-------------|
| **Drivers** | 100,000 | Active drivers in metro |
| **Vehicles** | 100,000 | 1:1 with drivers |
| **Active Rides** | 20,000 | Concurrent rides at peak |
| **Surge Zones** | 500 | Geographic pricing zones |
| **Local Users** | 2,000,000 | Users with home in this city |
| **Visiting Users** | 100,000 | Travelers currently in city |

#### Per-Driver Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | driver_id, name, phone, license_number | string |
| **Status** | online, available, on_trip, last_seen | string/numeric |
| **Location** | current_lat, current_lng, heading, speed | numeric |
| **Vehicle** | vehicle_id, make, model, color, plate | string |
| **Ratings** | avg_rating, total_trips, acceptance_rate | numeric |
| **Earnings** | today_earnings, week_earnings, bonus_progress | numeric |

#### Per-Ride Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | ride_id, rider_id, driver_id | string |
| **Status** | requested, matched, pickup, in_progress, completed | string |
| **Route** | pickup_lat, pickup_lng, dest_lat, dest_lng | numeric |
| **Timing** | request_time, pickup_time, eta_minutes | string/numeric |
| **Pricing** | base_fare, surge_multiplier, estimated_total | numeric |

#### Per-User Attributes (Local Copy)

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | user_id, name, phone, email | string |
| **Status** | active_ride_id, last_ride_time | string |
| **Preferences** | preferred_vehicle_type, accessibility_needs | string |
| **Payment** | default_payment_method, payment_verified | string |
| **Ratings** | rider_rating, total_rides | numeric |

#### City Entity Count Summary

| Entity Type | Count | String Attrs | Numeric Attrs | Total Attrs |
|-------------|-------|--------------|---------------|-------------|
| Drivers | 100,000 | 8 | 10 | 1,800,000 |
| Vehicles | 100,000 | 6 | 2 | 800,000 |
| Active Rides | 20,000 | 6 | 8 | 280,000 |
| Surge Zones | 500 | 2 | 4 | 3,000 |
| Local Users | 2,000,000 | 6 | 3 | 18,000,000 |
| Visiting Users | 100,000 | 6 | 3 | 900,000 |
| **Total** | **~2.3M** | | | **~21.8M** |

#### City-Level Performance

| Metric | Value |
|--------|-------|
| **Entities** | ~2.3 million |
| **Total Attributes** | ~21.8 million |
| **Comparison to Mendoza** | ~1.1x entities, ~1.1x attributes |

**Conclusion**: A large city (NYC, London, São Paulo) with 100K drivers and 2M local users is **roughly equivalent to the mendoza benchmark**—well within a single arkiv chain's capacity.

### Tier 2: Regional User Index

Regional servers maintain user profiles for their geography.

| Region | Users | Drivers | Major Cities |
|--------|-------|---------|--------------|
| North America | ~50M | ~1.8M | 200+ cities |
| Latin America | ~40M | ~1.5M | 150+ cities |
| Europe/Middle East | ~35M | ~1.2M | 300+ cities |
| Asia Pacific | ~25M | ~0.9M | 200+ cities |

Each region would need **multiple arkiv chains** for user data:
- ~50M users × 9 attrs = ~450M attributes per region
- Split across 5-10 regional chains = ~50M attrs each

### Tier 3: Global User Directory

Lightweight lookup for cross-region travelers:

| Attribute | Type | Purpose |
|-----------|------|---------|
| user_id | string | Global unique ID |
| home_region | string | Where profile lives |
| payment_verified | string | Can they pay? |
| rider_rating | numeric | Trust score |
| last_active_region | string | For routing |

~150M users × 5 attrs = ~750M attributes globally

This could be:
- A single large arkiv chain (pushing limits)
- Sharded by user_id hash across 10 chains (~75M attrs each)

### Scaling Summary

| Tier | Scope | Arkiv Chains | Entities/Chain | Attrs/Chain |
|------|-------|--------------|----------------|-------------|
| City | Metro area | ~500 (major cities) | ~2M | ~20M |
| Region | Continent | ~20-40 | ~10-50M | ~100-500M |
| Global | Directory | ~10 | ~15M | ~75M |

### Update Frequency

| Operation | Frequency | Scope |
|-----------|-----------|-------|
| Driver location | Every 3-5 sec | City chain |
| Ride status | Every 10-30 sec | City chain |
| Surge pricing | Every 1-5 min | City chain |
| User profile update | Occasional | Regional chain |
| Cross-region lookup | Per trip request | Global directory |

For a city with 100K drivers updating location every 5 seconds:
- **Location updates**: ~20K updates/sec
- **Ride updates**: ~2K updates/sec (20K active rides / 10 sec)
- **Total**: ~22K attribute updates/sec

This is **within arkiv's steady-state capacity** (~6,400 rows/sec with full entities, higher for attribute-only updates).

---

### V1 Target: 5 Regions, ≤6 DB Chains

For a minimum viable decentralized ride-sharing platform, we target **5 global regions with at most 6 arkiv chains total**. This forces us to validate whether SQLite/arkiv can handle regional-scale data in a single chain.

#### Regional Setup

| Region | Users | Drivers | Rides/Day | Chain |
|--------|-------|---------|-----------|-------|
| **North America** | 50M | 1.8M | 8M | Chain 1 |
| **Latin America** | 40M | 1.5M | 6M | Chain 2 |
| **Europe/Middle East** | 35M | 1.2M | 5M | Chain 3 |
| **Asia Pacific** | 25M | 0.9M | 6M | Chain 4 |
| **Global Directory** | 150M | — | — | Chain 5 |
| **Reserve/Overflow** | — | — | — | Chain 6 |

#### Per-Region Chain Requirements

| Metric | North America | Latin America | EMEA | APAC |
|--------|---------------|---------------|------|------|
| **Drivers** | 1.8M | 1.5M | 1.2M | 0.9M |
| **Users** | 50M | 40M | 35M | 25M |
| **Active Rides (peak)** | 500K | 400K | 300K | 400K |
| **Total Entities** | ~52M | ~42M | ~37M | ~26M |
| **String Attrs** | ~400M | ~320M | ~280M | ~200M |
| **Numeric Attrs** | ~300M | ~240M | ~210M | ~150M |
| **Total Attrs** | ~700M | ~560M | ~490M | ~350M |

#### Global Directory Chain Requirements

| Metric | Value |
|--------|-------|
| **Users** | 150M |
| **Attributes per user** | 5 (minimal: id, home_region, payment_ok, rating, last_region) |
| **Total Attributes** | ~750M |

#### V1 Validation Questions

These are the key questions that need experimental validation:

| Question | Current Benchmark | V1 Requirement | Gap |
|----------|-------------------|----------------|-----|
| Max entities per chain? | 800K (mendoza) | 52M (North America) | **65x** |
| Max attributes per chain? | 19M (mendoza) | 700M (North America) | **37x** |
| Write throughput? | ~6,400 rows/sec | ~50K updates/sec (peak) | **8x** |
| Full sync time? | ~20 min (663K entities) | <1 hour (52M entities) | TBD |
| DB file size? | 13.35 GB (mendoza) | ~50-100 GB estimated | TBD |

#### Proposed Experiments

To validate SQLite as a V1-ready decision for decentralized ride-sharing:

| Experiment | Description | Success Criteria |
|------------|-------------|------------------|
| **Scale test: 10M entities** | Generate 10M driver/user entities with realistic attributes | Completes in <2 hours |
| **Scale test: 50M entities** | Full North America region simulation | Completes in <8 hours |
| **Write throughput: 50K/sec** | Sustained attribute updates at peak load | Maintains throughput for 10+ min |
| **Query latency: driver lookup** | Find available drivers in geographic area | <100ms for 10K driver result |
| **Query latency: user lookup** | Fetch user profile by ID | <10ms |
| **Recovery time** | Time to load 50M entity DB from disk | <5 min |
| **Concurrent writes** | Multiple writers (simulating distributed ingest) | No corruption, acceptable throughput |

#### V1 Architecture Decision Tree

```
Is SQLite viable for V1 decentralized ride-sharing?

1. Can a single chain handle 50M entities?
   ├─ YES → Proceed with 5-region architecture
   └─ NO → Need city-level sharding (500+ chains)

2. Can write throughput reach 50K updates/sec?
   ├─ YES → Real-time driver location updates feasible
   └─ NO → Need batching (5-10 sec location updates) or
           separate hot-path for locations

3. Can query latency stay <100ms for geo-queries?
   ├─ YES → Driver matching in arkiv
   └─ NO → Need separate geo-index (PostGIS, Redis Geo)

4. Is recovery time <5 min for 50M entities?
   ├─ YES → Acceptable failover
   └─ NO → Need warm standby or faster storage
```

#### Fallback Options

If experiments show SQLite cannot handle regional scale:

| Fallback | Description | Chains Needed |
|----------|-------------|---------------|
| **City sharding** | One chain per major metro | ~100-500 |
| **Country sharding** | One chain per country | ~50-70 |
| **Hybrid** | SQLite for drivers/rides, PostgreSQL for users | 5 SQLite + 5 PostgreSQL |
| **Tiered storage** | Hot data in SQLite, cold in S3/archive | 5 + archive layer |

#### Implementation Roadmap

| Phase | Scope | Entities | Chains | Goal |
|-------|-------|----------|--------|------|
| **Phase 1** | Single city (pilot) | ~2M | 1 | Validate city-scale |
| **Phase 2** | Single region (smallest) | ~26M | 1 | Validate APAC-scale |
| **Phase 3** | All regions | ~150M | 5 | Full V1 deployment |
| **Phase 4** | Global directory | +150M | 6 | Cross-region travel |

### Arkiv Requirements: Ride-Sharing

**City-Level Chain (V1 Pilot)**

| Requirement | Value | Notes |
|-------------|-------|-------|
| **Entities** | 2.3M | Per major city |
| **Attributes** | 21.8M | Drivers, users, rides |
| **Writes/sec** | 22K | Driver locations + ride updates |
| **Reads/sec** | 50K+ | Driver matching, surge pricing |
| **Chains needed** | 1 per city | ~500 for global coverage |
| **DB size estimate** | ~3-5 GB | Per city |
| **Sync time** | ~30 min | Full city sync |

**Regional Chain (V1 Target)**

| Requirement | Value | Notes |
|-------------|-------|-------|
| **Entities** | 52M | North America (largest region) |
| **Attributes** | 700M | Full user + driver profiles |
| **Writes/sec** | 50K | Peak load |
| **Reads/sec** | 100K+ | Cross-city lookups |
| **Chains needed** | 5-6 | Global coverage |
| **DB size estimate** | ~50-100 GB | Per region |
| **Sync time** | <1 hour | Target |

---

## Use Case 3: Decentralized Freelance/Gig Platform

### Context: The Gig Economy Problem

Current freelance platforms extract significant value from workers:

| Platform | Take Rate | Workers | Clients |
|----------|-----------|---------|---------|
| Upwork | 10-20% | ~18M registered | ~5M clients |
| Fiverr | 20% | ~4M sellers | ~4M buyers |
| Toptal | ~30-40% | ~10K vetted | Enterprise |
| 99designs | 15-50% | ~1M designers | SMB |

**Total addressable market**: ~$400B+ globally in freelance/gig work

### Why Decentralize?

| Problem | Impact | Decentralized Solution |
|---------|--------|------------------------|
| **High fees** | 20%+ extracted from workers | P2P matching, minimal fees |
| **No portability** | Reputation locked to platform | Portable, verifiable reputation |
| **Arbitrary deplatforming** | Workers lose livelihood overnight | Censorship-resistant identity |
| **Opaque algorithms** | Unclear how work is allocated | Transparent matching rules |
| **Payment delays** | 14-30 day holds common | Direct settlement |

### Entity Model

#### Core Entities

| Entity Type | Count (Global) | Description |
|-------------|----------------|-------------|
| **Workers** | 50,000,000 | Freelancers offering services |
| **Clients** | 10,000,000 | Businesses/individuals hiring |
| **Jobs** | 5,000,000 | Active job postings |
| **Contracts** | 20,000,000 | Active/recent engagements |
| **Skills** | 50,000 | Taxonomy of capabilities |
| **Reviews** | 100,000,000 | Reputation data |

#### Per-Worker Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | worker_id, name, verified_identity, joined_date | string |
| **Profile** | headline, bio, hourly_rate, availability | string/numeric |
| **Skills** | skill_ids, skill_levels, certifications | string |
| **Reputation** | avg_rating, total_jobs, completion_rate, response_time | numeric |
| **Earnings** | lifetime_earnings, current_balance, pending_payments | numeric |
| **Status** | available, busy, on_vacation, last_active | string |

#### Per-Job Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | job_id, client_id, title, category | string |
| **Requirements** | required_skills, experience_level, estimated_hours | string/numeric |
| **Budget** | budget_type (fixed/hourly), budget_amount, payment_verified | string/numeric |
| **Status** | draft, open, in_progress, completed, cancelled | string |
| **Matching** | proposals_count, shortlisted_workers, awarded_worker | string/numeric |

#### Per-Contract Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | contract_id, job_id, worker_id, client_id | string |
| **Terms** | rate, payment_schedule, milestones, deadline | string/numeric |
| **Status** | active, paused, completed, disputed | string |
| **Progress** | hours_logged, milestones_completed, amount_paid | numeric |
| **Escrow** | escrow_amount, release_conditions | string/numeric |

### Regional Architecture

Unlike ride-sharing, freelance work is **less geographically bound**—a developer in India can work for a client in the US. However, we can still regionalize by:

1. **Worker home region** — Where worker's primary profile lives
2. **Client region** — Where client operates
3. **Global skill index** — Cross-region worker discovery

#### Proposed Setup: 5 Regions + Global Index

| Region | Workers | Clients | Contracts | Chain |
|--------|---------|---------|-----------|-------|
| **North America** | 10M | 3M | 5M | Chain 1 |
| **Europe** | 12M | 2M | 4M | Chain 2 |
| **Asia** | 20M | 3M | 8M | Chain 3 |
| **Latin America** | 5M | 1M | 2M | Chain 4 |
| **Africa/ME** | 3M | 1M | 1M | Chain 5 |
| **Global Skill Index** | 50M worker refs | — | — | Chain 6 |

#### Per-Region Entity Count

| Region | Workers | Clients | Jobs | Contracts | Reviews | Total Entities | Total Attrs |
|--------|---------|---------|------|-----------|---------|----------------|-------------|
| Asia (largest) | 20M | 3M | 2M | 8M | 40M | **73M** | **~800M** |
| North America | 10M | 3M | 1.5M | 5M | 25M | **44.5M** | **~500M** |

### Update Patterns

| Operation | Frequency | Entities Affected |
|-----------|-----------|-------------------|
| Worker comes online | 1000s/min | Worker status |
| Job posted | 100s/min | New job entity |
| Proposal submitted | 1000s/min | Job + worker |
| Contract started | 100s/min | New contract + job status |
| Hours logged | 1000s/hour | Contract progress |
| Payment released | 100s/hour | Contract + worker earnings |
| Review submitted | 100s/hour | New review + reputation update |

**Estimated updates**: ~10K-50K attribute updates/sec globally, ~2K-10K per region

### V1 Target

| Metric | Target | Comparison to Mendoza |
|--------|--------|----------------------|
| Entities per region | ~50M | 62x |
| Attributes per region | ~500M | 26x |
| Updates/sec | ~10K | ~1.5x current benchmark |

### Key Advantages for Arkiv

- **Event-driven updates** — No real-time location tracking
- **Clear entity lifecycle** — Jobs and contracts have well-defined states
- **Reputation is key** — Immutable history is valuable
- **Cross-region queries are rare** — Most work is regional

### Arkiv Requirements: Freelance Platform

**Regional Chain (V1 Target)**

| Requirement | Value | Notes |
|-------------|-------|-------|
| **Entities** | 50M | Per region (Asia largest) |
| **Attributes** | 500M | Workers, clients, jobs, contracts, reviews |
| **Writes/sec** | 2K-10K | Job lifecycle events |
| **Reads/sec** | 20K-50K | Worker search, job matching |
| **Chains needed** | 6 | 5 regions + global skill index |
| **DB size estimate** | ~30-50 GB | Per region |
| **Sync time** | ~1 hour | Full region sync |

---

## Use Case 4: Decentralized Supply Chain Tracking

### Context: Supply Chain Transparency Problem

Global supply chains involve multiple untrusting parties:

| Stage | Parties | Trust Issues |
|-------|---------|--------------|
| Raw materials | Miners, farmers, suppliers | Origin fraud, conflict materials |
| Manufacturing | Factories, assemblers | Labor conditions, quality |
| Logistics | Carriers, warehouses, customs | Theft, counterfeits, delays |
| Retail | Distributors, retailers | Authenticity, recalls |
| Consumer | End buyers | Provenance verification |

**Market size**: ~$3T+ in goods requiring traceability (food, pharma, luxury, electronics)

### Why Decentralize?

| Problem | Impact | Decentralized Solution |
|---------|--------|------------------------|
| **Single source of truth** | Each party has own records | Shared immutable ledger |
| **Provenance disputes** | "He said, she said" | Timestamped, signed attestations |
| **Counterfeit goods** | $500B+ annual losses | Verifiable product history |
| **Recall efficiency** | Days to trace contamination | Minutes with full chain |
| **Compliance burden** | Duplicated audits | Shared certifications |

### Entity Model

#### Core Entities

| Entity Type | Count (Regional) | Description |
|-------------|------------------|-------------|
| **Products/SKUs** | 10,000,000 | Unique product types |
| **Product Instances** | 100,000,000 | Individual serialized items |
| **Facilities** | 100,000 | Factories, warehouses, ports |
| **Organizations** | 50,000 | Companies in the chain |
| **Shipments** | 10,000,000 | Active shipments |
| **Certifications** | 1,000,000 | Organic, fair trade, etc. |
| **Events** | 500,000,000 | Checkpoint attestations |

#### Per-Product Instance Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | instance_id, sku, serial_number, batch_id | string |
| **Origin** | manufacturer_id, facility_id, production_date | string |
| **Current State** | location, custodian, status | string |
| **Chain** | previous_custodians, custody_transfers | string |
| **Certifications** | cert_ids, expiry_dates | string |

#### Per-Shipment Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | shipment_id, carrier_id, bill_of_lading | string |
| **Contents** | product_instance_ids, quantity, weight_kg | string/numeric |
| **Route** | origin_facility, destination_facility, checkpoints | string |
| **Status** | in_transit, at_checkpoint, delivered, exception | string |
| **Conditions** | temperature_ok, humidity_ok, tamper_evident | string |

#### Per-Event Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | event_id, event_type, timestamp | string |
| **Subject** | product_instance_id, shipment_id | string |
| **Location** | facility_id, geo_coordinates | string/numeric |
| **Attestation** | attester_org_id, signature, evidence_hash | string |

### Regional Architecture

Supply chains are naturally regional with cross-border handoffs:

| Region | Products | Instances | Facilities | Events/Day | Chain |
|--------|----------|-----------|------------|------------|-------|
| **Asia-Pacific** | 4M | 40M | 40K | 10M | Chain 1 |
| **Europe** | 3M | 30M | 30K | 8M | Chain 2 |
| **North America** | 2M | 20M | 20K | 5M | Chain 3 |
| **Latin America** | 0.5M | 5M | 5K | 1M | Chain 4 |
| **Africa/ME** | 0.5M | 5M | 5K | 1M | Chain 5 |

#### Per-Region Entity Count (Asia-Pacific Example)

| Entity Type | Count | Attrs/Entity | Total Attrs |
|-------------|-------|--------------|-------------|
| Products | 4M | 10 | 40M |
| Instances | 40M | 8 | 320M |
| Facilities | 40K | 12 | 480K |
| Organizations | 20K | 10 | 200K |
| Shipments | 5M | 10 | 50M |
| Events (30 days) | 300M | 6 | 1.8B |
| **Total** | **~350M** | | **~2.2B** |

### Update Patterns

| Operation | Frequency | Description |
|-----------|-----------|-------------|
| Checkpoint scan | 10M/day | Product scanned at facility |
| Shipment created | 100K/day | New shipment entity |
| Custody transfer | 1M/day | Product changes hands |
| Certification issued | 10K/day | New cert attached to product |
| Exception flagged | 10K/day | Temperature breach, delay, etc. |

**Estimated updates**: ~500 events/sec per region (checkpoint-based, not real-time)

### V1 Target: Focused Vertical

Rather than all supply chains, start with a specific vertical:

| Vertical | Products | Instances | Update Rate | Complexity |
|----------|----------|-----------|-------------|------------|
| **Pharmaceuticals** | 100K | 10M | Low | High value |
| **Food/Produce** | 500K | 50M | Medium | Perishable |
| **Luxury Goods** | 50K | 5M | Low | High value |
| **Electronics** | 200K | 20M | Medium | Counterfeit risk |

**Recommended V1**: Pharmaceuticals or Luxury Goods (lower volume, high value per item)

| Metric | Pharma V1 | Comparison to Mendoza |
|--------|-----------|----------------------|
| Products | 100K | 0.1x |
| Instances | 10M | 12x |
| Events (30 days) | 50M | 62x |
| Updates/sec | ~50 | Well within capacity |

### Key Advantages for Arkiv

- **Checkpoint updates** — Not real-time, batch-friendly
- **Multi-party attestation** — Natural fit for decentralized trust
- **Immutable history** — Core value proposition
- **Clear regulatory drivers** — FDA, EU traceability mandates

### Arkiv Requirements: Supply Chain

**Full Regional Chain**

| Requirement | Value | Notes |
|-------------|-------|-------|
| **Entities** | 350M | Asia-Pacific (largest), incl. 30-day events |
| **Attributes** | 2.2B | Products, instances, shipments, events |
| **Writes/sec** | 500 | Checkpoint scans, custody transfers |
| **Reads/sec** | 5K-10K | Provenance queries, compliance checks |
| **Chains needed** | 5 | Regional supply chain networks |
| **DB size estimate** | ~100-200 GB | Per region with event history |
| **Sync time** | ~3-4 hours | Full region sync |

**V1 Vertical (Pharmaceuticals)**

| Requirement | Value | Notes |
|-------------|-------|-------|
| **Entities** | 60M | Products + instances + 30-day events |
| **Attributes** | 600M | Focused vertical |
| **Writes/sec** | 50 | Lower volume, high value |
| **Reads/sec** | 1K | Compliance, recall queries |
| **Chains needed** | 1-2 | Single vertical |
| **DB size estimate** | ~10-20 GB | Manageable |
| **Sync time** | ~1 hour | Full sync |

---

## Use Case 5: Decentralized Energy Grid (P2P Trading)

### Context: The Energy Transition

The shift to distributed energy creates new challenges:

| Trend | Impact | Scale |
|-------|--------|-------|
| **Rooftop solar** | Millions of small producers | 3M+ US homes with solar |
| **Battery storage** | Prosumers can time-shift | 500K+ home batteries |
| **EVs as storage** | Vehicle-to-grid potential | 40M+ EVs by 2030 |
| **Smart meters** | Real-time consumption data | 100M+ deployed |

**Problem**: Centralized utilities weren't designed for bidirectional flow and millions of small producers.

### Why Decentralize?

| Problem | Impact | Decentralized Solution |
|---------|--------|------------------------|
| **Utility monopoly** | Fixed prices, no choice | P2P market pricing |
| **Wasted excess** | Solar sold back at low rates | Direct neighbor sales |
| **Grid congestion** | Centralized balancing | Local microgrids |
| **Slow settlement** | Monthly billing | Near-real-time settlement |
| **Opaque pricing** | Complex tariffs | Transparent market |

### Entity Model

#### Core Entities

| Entity Type | Count (Regional Grid) | Description |
|-------------|----------------------|-------------|
| **Meters** | 10,000,000 | Smart meters (prosumers + consumers) |
| **Producers** | 1,000,000 | Solar, wind, battery owners |
| **Consumers** | 9,000,000 | Households, businesses |
| **Grid Segments** | 10,000 | Local grid areas |
| **Trades** | 50,000,000/month | Energy buy/sell transactions |
| **Readings** | 300,000,000/month | Meter data (15-min intervals) |

#### Per-Meter Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | meter_id, owner_id, location, grid_segment | string |
| **Capabilities** | is_producer, is_consumer, battery_capacity_kwh | string/numeric |
| **Current State** | current_production_kw, current_consumption_kw | numeric |
| **Cumulative** | total_produced_kwh, total_consumed_kwh, total_traded_kwh | numeric |
| **Pricing** | sell_price_kwh, buy_price_kwh, auto_trade_enabled | numeric |

#### Per-Trade Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | trade_id, seller_meter, buyer_meter, timestamp | string |
| **Terms** | quantity_kwh, price_per_kwh, total_amount | numeric |
| **Status** | matched, confirmed, settled, disputed | string |
| **Grid** | grid_segment, transmission_fee | string/numeric |

#### Per-Reading Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | reading_id, meter_id, timestamp | string |
| **Values** | production_kwh, consumption_kwh, net_kwh | numeric |
| **Quality** | validated, estimated, meter_status | string |

### Regional Architecture

Energy grids are inherently regional/national:

| Region | Meters | Producers | Readings/Day | Chain |
|--------|--------|-----------|--------------|-------|
| **California** | 15M | 2M | 1.4B | Chain 1 |
| **Texas (ERCOT)** | 12M | 1M | 1.2B | Chain 2 |
| **Germany** | 20M | 2M | 2B | Chain 3 |
| **Australia** | 10M | 3M | 1B | Chain 4 |
| **UK** | 30M | 1M | 2.8B | Chain 5 |

#### Per-Region Entity Count (California Example)

| Entity Type | Count | Attrs/Entity | Total Attrs |
|-------------|-------|--------------|-------------|
| Meters | 15M | 12 | 180M |
| Grid Segments | 5K | 8 | 40K |
| Trades (30 days) | 100M | 8 | 800M |
| Readings (7 days) | 10B | 5 | 50B |
| **Total** | **~10B** | | **~51B** |

**Challenge**: Readings volume is massive. Need tiered approach.

### Tiered Storage Strategy

| Tier | Data | Retention | Storage |
|------|------|-----------|---------|
| **Hot (Arkiv)** | Meters, current state, recent trades | Indefinite | ~200M attrs |
| **Warm** | Readings (7 days), trade history (30 days) | Rolling | Separate DB |
| **Cold** | Historical readings, old trades | Years | Archive |

#### Arkiv-Only Scope

| Entity Type | Count | Attrs | Notes |
|-------------|-------|-------|-------|
| Meters | 15M | 180M | Full state |
| Grid Segments | 5K | 40K | Aggregated metrics |
| Active Trades | 1M | 8M | Current day |
| **Total** | **~16M** | **~190M** | Manageable |

### Update Patterns

| Operation | Frequency | Description |
|-----------|-----------|-------------|
| Meter reading | Every 15 min | 15M × 4/hour = 1M/hour |
| Trade matched | 1000s/min | New trade entities |
| Trade settled | 1000s/min | Status updates |
| Price update | Every 5 min | Segment pricing |

**Estimated updates** (Arkiv hot tier): ~5K-10K/sec (excluding raw readings)

### V1 Target: Single Grid Region

| Metric | California V1 | Comparison to Mendoza |
|--------|---------------|----------------------|
| Meters | 15M | 19x entities |
| Active attrs | 190M | 10x attributes |
| Updates/sec | ~10K | 1.5x benchmark |

### Key Advantages for Arkiv

- **15-minute intervals** — Not real-time, batch-friendly
- **Settlement immutability** — Trade history is valuable
- **Multi-party** — Producers, consumers, grid operators
- **Growing market** — Regulatory push for P2P energy

### Arkiv Requirements: Energy Grid

**Hot Tier Only (V1 Target)**

| Requirement | Value | Notes |
|-------------|-------|-------|
| **Entities** | 16M | Meters + grid segments + active trades |
| **Attributes** | 190M | Current state, no historical readings |
| **Writes/sec** | 5K-10K | Trade matching, meter state updates |
| **Reads/sec** | 10K-20K | Price discovery, balance queries |
| **Chains needed** | 1 per grid | ~5-10 for major markets |
| **DB size estimate** | ~10-20 GB | Hot data only |
| **Sync time** | ~30 min | Full grid sync |

**Note**: Raw meter readings (billions/day) stored in separate warm/cold tier, not in arkiv.

---

## Use Case 6: Decentralized Carbon Credits

### Context: The Carbon Market Problem

Carbon markets are plagued by opacity and fraud:

| Issue | Impact | Examples |
|-------|--------|----------|
| **Double counting** | Same offset sold twice | Verra registry issues |
| **Phantom credits** | Credits for non-existent projects | Forest projects on farmland |
| **Greenwashing** | Low-quality offsets marketed as premium | Cheap REDD+ credits |
| **Verification gaps** | Inconsistent auditing | Self-reported baselines |
| **Market fragmentation** | Different registries don't interoperate | Verra, Gold Standard, ACR |

**Market size**: ~$2B voluntary market, ~$100B+ compliance markets (EU ETS, etc.)

### Why Decentralize?

| Problem | Impact | Decentralized Solution |
|---------|--------|------------------------|
| **Registry silos** | No unified view | Shared cross-registry ledger |
| **Double counting** | Fraud, market distrust | Unique, trackable credit IDs |
| **Verification opacity** | Can't audit the auditors | Public attestation chain |
| **Retirement fraud** | "Zombie" credits reused | Immutable retirement records |
| **Price discovery** | Opaque OTC markets | Transparent trading |

### Entity Model

#### Core Entities

| Entity Type | Count (Global) | Description |
|-------------|----------------|-------------|
| **Projects** | 50,000 | Offset-generating projects |
| **Credits** | 2,000,000,000 | Individual carbon credits (tonnes) |
| **Organizations** | 100,000 | Project developers, buyers, verifiers |
| **Verifiers** | 500 | Accredited verification bodies |
| **Issuances** | 500,000 | Credit creation events |
| **Retirements** | 100,000,000 | Credits permanently retired |
| **Trades** | 50,000,000 | Ownership transfers |

#### Per-Project Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | project_id, name, developer_org, registry_origin | string |
| **Location** | country, region, coordinates, area_hectares | string/numeric |
| **Type** | methodology, project_type (forest, renewable, etc.) | string |
| **Status** | registered, active, suspended, completed | string |
| **Metrics** | total_credits_issued, credits_available, vintage_years | numeric |
| **Verification** | last_verification_date, verifier_id, verification_docs | string |

#### Per-Credit Attributes (Batch-Level)

Rather than 2B individual credit entities, track batches:

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | batch_id, project_id, vintage_year, serial_range | string |
| **Quantity** | credits_in_batch, credits_retired, credits_available | numeric |
| **Quality** | methodology_version, additionality_score, co_benefits | string/numeric |
| **Custody** | current_owner_org, custody_history | string |
| **Status** | available, reserved, retired | string |

#### Per-Retirement Attributes

| Attribute Category | Attributes | Type |
|--------------------|------------|------|
| **Identity** | retirement_id, batch_id, retiring_org | string |
| **Details** | credits_retired, retirement_reason, beneficiary | string/numeric |
| **Attestation** | timestamp, registry_confirmation, evidence_hash | string |

### Architecture: Single Global Chain

Unlike regional use cases, carbon credits are **inherently global**—a credit from a Brazilian forest project might be bought by a European airline and retired for a US company's net-zero claim.

| Approach | Chains | Rationale |
|----------|--------|-----------|
| **Single global chain** | 1 | Credits must be globally unique |
| **+ Regional caches** | 5 | Read replicas for performance |

#### Global Entity Count

| Entity Type | Count | Attrs/Entity | Total Attrs |
|-------------|-------|--------------|-------------|
| Projects | 50K | 15 | 750K |
| Credit Batches | 10M | 10 | 100M |
| Organizations | 100K | 12 | 1.2M |
| Verifiers | 500 | 10 | 5K |
| Retirements | 100M | 8 | 800M |
| Trades | 50M | 8 | 400M |
| **Total** | **~160M** | | **~1.3B** |

### Update Patterns

| Operation | Frequency | Description |
|-----------|-----------|-------------|
| Credit issuance | 1000s/day | New batches from verified projects |
| Trade | 10K/day | Ownership transfers |
| Retirement | 50K/day | Permanent retirement |
| Verification | 100s/day | Project verification events |
| Price update | Continuous | Market data |

**Estimated updates**: ~100-500/sec (very manageable)

### V1 Target: Voluntary Market Focus

Start with the voluntary carbon market (simpler, less regulated):

| Metric | Voluntary V1 | Comparison to Mendoza |
|--------|--------------|----------------------|
| Projects | 20K | 0.025x entities |
| Batches | 2M | 2.5x entities |
| Organizations | 50K | 0.06x entities |
| Retirements | 20M | 25x entities |
| Total entities | ~22M | 27x |
| Total attrs | ~250M | 13x |
| Updates/sec | ~50 | Well within capacity |

### Key Advantages for Arkiv

- **Low update frequency** — Event-driven, not real-time
- **Immutability critical** — Core value proposition for trust
- **Global uniqueness** — Natural single-chain model
- **High visibility** — ESG, climate tech interest
- **Clear standards** — Existing methodologies to follow

### Arkiv Requirements: Carbon Credits

**Global Chain (V1 Target: Voluntary Market)**

| Requirement | Value | Notes |
|-------------|-------|-------|
| **Entities** | 22M | Projects, batches, orgs, retirements |
| **Attributes** | 250M | Full credit lifecycle |
| **Writes/sec** | 50-500 | Issuances, trades, retirements |
| **Reads/sec** | 1K-5K | Verification, ownership queries |
| **Chains needed** | 1 | Global (+ regional caches) |
| **DB size estimate** | ~15-25 GB | Voluntary market |
| **Sync time** | ~30-45 min | Full global sync |

**Full Market (Voluntary + Compliance)**

| Requirement | Value | Notes |
|-------------|-------|-------|
| **Entities** | 160M | Including compliance markets |
| **Attributes** | 1.3B | All registries unified |
| **Writes/sec** | 100-500 | Still event-driven |
| **Reads/sec** | 5K-10K | Higher query volume |
| **Chains needed** | 1 | Single source of truth |
| **DB size estimate** | ~80-100 GB | Full market |
| **Sync time** | ~2-3 hours | Full sync |

---

## Mendoza Benchmark Reference

> **See [`mendoza-dump.md`](./mendoza-dump.md) for complete analysis** including:
> - Dataset characteristics & database schema
> - Entity ownership distribution
> - Storage breakdown by table
> - Attribute distribution & common keys
> - Payload size distribution with histograms
> - Performance metrics & scaling multipliers

**Quick reference** (800K entities, 20.5M rows, 13.35 GB, ~27 days of data):

| Metric | Value |
|--------|-------|
| **Throughput** | ~700 entities/sec, ~6,400 rows/sec |
| **Storage** | 60% data / 40% indexes |
| **Bottleneck** | String attributes (0.95 correlation) |
| **Block time** | 2 seconds |

---

## Mendoza vs Use Case Alignment

How well does the mendoza dataset align with the proposed use cases?

### Good Alignment ✅

| Aspect | Mendoza Reality | Use Case Match |
|--------|-----------------|----------------|
| **Entity scale (800K)** | Proven capacity | Compute Marketplace (663K) fits perfectly |
| **Attributes/entity (~24)** | Realistic distribution | Compute Marketplace (22), Carbon (11) similar |
| **Bi-temporal storage** | `from_block`/`to_block` tracking | Supply Chain provenance, Carbon retirement history |
| **Event-driven updates** | ~700 entities/sec, not real-time | Carbon Credits (~500/sec), Supply Chain (~500/sec) |
| **EAV schema flexibility** | 134 distinct entity types | Multi-entity use cases supported |
| **JSON payloads (94.6%)** | Dominant content type | Common across all use cases |
| **Entity diversity** | 69+ hackathon project types | Validates multi-tenant chain model |
| **Continuous operation** | 27 days of live data | Not a bulk dump—real usage patterns |

### Areas Needing Validation ⚠️

| Aspect | Mendoza Reality | Use Case Gap |
|--------|-----------------|--------------|
| **Write throughput** | ~6,400 rows/sec (bulk-ish) | Ride-Share needs 22K-50K sustained |
| **Query patterns** | No query benchmarks | All use cases need complex queries |
| **Concurrent writers** | Single-writer mode | Multi-node ingest not tested |
| **Scale multipliers** | 1x proven | Carbon V1 (27x), Energy Grid (20x) need validation |

### Notable Differences 🔴

| Aspect | Mendoza Reality | Use Case Assumption |
|--------|-----------------|---------------------|
| **Real-time requirements** | None | Ride-Share: driver location every 3-5 sec |
| **Entity relationships** | Buried in string attributes | Use cases assume efficient joins |
| **Geographic sharding** | Single chain | Ride-Share assumes 500 city chains |
| **State-machine updates** | 97% create-only | Use cases expect lifecycle transitions |

### Quantitative Comparison

| Metric | Mendoza | Compute Marketplace | Carbon V1 | Energy Grid | Ride-Share City |
|--------|---------|-------------|-----------|-------------|-----------------|
| **Entities** | 800K | 663K (0.83x) ✅ | 22M (27x) ⚠️ | 16M (20x) ⚠️ | 2.3M (2.9x) ✅ |
| **Attributes** | 19.4M | 13.9M (0.72x) ✅ | 250M (13x) ⚠️ | 190M (10x) ⚠️ | 21.8M (1.1x) ✅ |
| **Entity types** | 134 | 14 | 7 | 4 | 6 |
| **Writes/sec** | ~6,400 | 10K-50K ⚠️ | 50-500 ✅ | 5K-10K ✅ | 22K ⚠️ |

### Validation Recommendations

| Action | Priority | Rationale |
|--------|----------|-----------|
| **Add query benchmarks** | Critical | All use cases require read performance data |
| **Test sustained writes** | High | Validate steady-state writes for Compute Marketplace |
| **Implement entity relations** | High | Current string-based refs are inefficient |
| **Test 10x scale** | Medium | Validate Carbon/Energy Grid feasibility |
| **Add state-machine tests** | Medium | Lifecycle transitions (Ride, Job, Order) |

---

## Requirements Summary

### SQLite Performance Characteristics

SQLite has practical limits that affect use case viability. While official limits are enormous (281 TB database, 2^64 rows), real-world performance degrades at scale:

#### Row Count Performance Zones

| Row Count | Performance | Query Speed | Write Speed | Recommendation |
|-----------|-------------|-------------|-------------|----------------|
| <10M | 🟢 Excellent | Instant | Fast | No tuning needed |
| 10-50M | 🟢 Very Good | Fast | Good | Minimal tuning |
| 50-200M | 🟡 Good | Acceptable | Needs tuning | Careful indexing, WAL mode |
| 200-500M | 🟡 Challenging | Slower | Expert tuning | Consider partitioning |
| >500M | 🔴 Difficult | Degraded | Problematic | Sharding or PostgreSQL |

#### Key Factors

| Factor | Impact | Mitigation |
|--------|--------|------------|
| **Index size vs RAM** | Indexes in memory = fast queries | Ensure indexes fit in available RAM |
| **Write patterns** | Many small transactions = slow | Batch writes, larger transactions |
| **Row width** | Wide rows = slower scans | Keep rows narrow (EAV is good) |
| **Concurrent writes** | Single writer only | WAL mode for read concurrency |

#### Real-World SQLite Deployments

| Use Case | Rows | DB Size | Notes |
|----------|------|---------|-------|
| Expensify | 1B+ | 100+ GB | Heavy optimization |
| Cloudflare D1 | Millions | 10 GB limit | Edge constraints |
| Typical comfort | <100M | <50 GB | No special tuning |

### Comparison Across Use Cases

| Use Case | Entities | Attributes | Rows | SQLite Fit | Writes/sec | Reads/sec | Chains | DB Size |
|----------|----------|------------|------|------------|------------|-----------|--------|---------|
| **Compute Marketplace** | 663K | 15.3M | 15.3M | 🟢 | 170-1,600 | 120-1,500 | 🟢 1/network | 🟢 ~12 GB |
| **Ride-Sharing (City)** | 2.3M | 21.8M | 22M | 🟢 | 22K | 50K+ | 🔴 500 | 🟢 ~15 GB |
| **Ride-Sharing (Region)** | 52M | 700M | 700M | 🔴 | 50K | 100K+ | 🟢 6 | 🟢 ~500 GB |
| **Freelance Platform** | 50M | 500M | 500M | 🟡 | 2K-10K | 20K-50K | 🟢 6 | 🟢 ~350 GB |
| **Supply Chain (Full)** | 350M | 2.2B | 2.2B | 🔴 | 500 | 5K-10K | 🟢 5 | 🟢 ~1.5 TB |
| **Supply Chain (V1)** | 60M | 600M | 600M | 🟡 | 50 | 1K | 🟢 1-2 | 🟢 ~400 GB |
| **Energy Grid** | 16M | 190M | 190M | 🟡 | 5K-10K | 10K-20K | 🟢 5-10 | 🟢 ~130 GB |
| **Carbon Credits (V1)** | 22M | 250M | 250M | 🟡 | 50-500 | 1K-5K | 🟢 1 | 🟢 ~170 GB |
| **Carbon Credits (Full)** | 160M | 1.3B | 1.3B | 🔴 | 100-500 | 5K-10K | 🟢 1 | 🟢 ~900 GB |

> **DB Size Calculation**: Based on mendoza benchmark (20.5M rows = 13.35 GB, ~0.65 KB/row avg). Attribute-heavy workloads have ~40% index overhead.

**Legend:**
- **SQLite Fit**: 🟢 <50M rows | 🟡 50-500M rows | 🔴 >500M rows
- **Chains**: 🟢 <10 chains | 🟡 10-20 chains | 🔴 >20 chains
- **DB Size**: 🟢 All sizes within SQLite limits (281 TB max)

### Comparison to Current Benchmarks (Mendoza)

| Metric | Mendoza Benchmark | Best Fit | Stretch | Beyond Current |
|--------|-------------------|----------|---------|----------------|
| **Entities** | 800K | Compute Marketplace (663K) | Carbon V1 (22M) | Ride-Share Region (52M) |
| **Attributes** | 19.4M | Compute Marketplace (13.9M) | Energy Grid (190M) | Ride-Share Region (700M) |
| **Writes/sec** | 6.4K | Carbon (500) | Energy (10K) | Ride-Share (50K) |

### Use Case Fit Assessment

| Use Case | Within Current Capacity | Needs 10x Scale | Needs 50x+ Scale |
|----------|-------------------------|-----------------|------------------|
| **Compute Marketplace** | ✅ Perfect fit | — | — |
| **Carbon Credits V1** | ⚠️ 27x entities | — | — |
| **Energy Grid** | ⚠️ 20x entities | — | — |
| **Supply Chain V1** | ⚠️ 75x entities | — | — |
| **Freelance Platform** | — | ⚠️ 26x attrs | — |
| **Ride-Sharing City** | ✅ ~1x mendoza | — | — |
| **Ride-Sharing Region** | — | — | ❌ 37x attrs |

### Recommended Priority Order

Based on arkiv fit and strategic value:

| Priority | Use Case | Rationale |
|----------|----------|-----------|
| **1** | Compute Marketplace | Perfect fit, existing relationships (Golem), validates arkiv |
| **2** | Carbon Credits V1 | Low writes, high value, single global chain |
| **3** | Energy Grid | Regional fit, growing market, 15-min intervals |
| **4** | Supply Chain V1 | Checkpoint-based, pharma/luxury vertical |
| **5** | Freelance Platform | Event-driven, but needs scale validation |
| **6** | Ride-Sharing | Compelling story, but requires significant scale-up |

---

# Compute Marketplace Performance Requirements Framework

## Context: 300K Node Compute Marketplace

A realistic performance model for a 300K node decentralized compute marketplace with workload scheduling.

## 1. Write Performance Requirements

### 1.1 Node Entity Updates

**Model Parameters:**
- 300K nodes (physical capacity)
- 300K total workloads in system (steady state with housekeeping)
- 195K workloads running at any time (65% node utilization)
- 105K workloads pending (waiting for nodes)
- Average workload duration: 5 hours
- **Workload throughput: 195K running / 5h = 39K/hour = 10.8 workloads/sec**

**Marketplace Dynamics:** This model represents a **balanced marketplace** where supply and demand are in reasonable equilibrium. The 105K pending queue means ~2.7 hours to clear if no new workloads arrive. Alternative scenarios:

| Scenario | Available Nodes | Pending Queue | Queue Depth | Market State |
|----------|----------------|---------------|-------------|-------------|
| **Demand-Heavy** | 30K (30%) | 400K | ~20 hours | Under-supplied, high prices |
| **Balanced Market** (modeled) | 90K (30%) | 105K | ~2.7 hours | Healthy equilibrium |
| **Supply-Heavy** | 150K (50%) | 5K | <30 min | Over-supplied, low prices, idle nodes |

The balanced model represents a mature marketplace with healthy queue depths and reasonable wait times. Dynamic pricing helps maintain this equilibrium by adjusting to supply/demand fluctuations.

**Node Status Update Drivers:**

```
Workload assignments:  10.8 nodes/sec (available → busy)
Workload completions:  10.8 nodes/sec (busy → available)
Hardware failures:     1.5 nodes/sec (any → offline)
Maintenance returns:   1.5 nodes/sec (offline → available)
                       ────────────────────────────────────
Node status changes:   ~24 nodes/sec
```

**Other Node Updates:**

| Update Type | Frequency | Trigger | Entities/Update | Notes |
|-------------|-----------|---------|-----------------|-------|
| **Status Changes** | Continuous | Workload lifecycle + failures | ~24 nodes/sec | Driven by 10.8 workloads/sec throughput |
| **Health Metrics** | Conditional | Metrics change >10% | ~30-45 nodes/sec | Event-driven: cpu_util, mem_util, disk_util |
| **Config Updates** | Hourly-Daily | Software updates, patches | 300-15000 nodes | Rolling updates, staged deployments |
| **Hardware Changes** | Weekly-Monthly | Physical maintenance | 30-300 nodes | Rack moves, upgrades, decommissions |

**Health Metrics Strategy:** Each node checks its health every minute (CPU, memory, disk utilization). Updates are only written when metrics change substantially (>10 percentage points) or cross critical thresholds (>80%, >90%). This event-driven approach captures important state changes while avoiding redundant writes for stable nodes. Most nodes (70%) have stable workloads and write infrequently, while active or problematic nodes write more often, providing real-time visibility where it matters.

**Write Load Calculation:**

```
Status changes:        24 nodes/sec (includes matching: available→busy, busy→available)
Health metrics:        30-45 nodes/sec (conditional writes on substantial change)
Config updates:        1,500 nodes / 3600 sec = 0.42 nodes/sec
                       ───────────────────────────────────
Total steady-state:    ~54-69 node entities/sec
```

**Peak scenarios:**
- **Mass node failure**: 3,000 nodes in <10 sec = **300 nodes/sec spike** (status + health)
- **Rolling restart**: 30K nodes/hour = **8 nodes/sec** for 1 hour

### 1.2 Workload Entity Creations/Updates

**Operational Dynamics:**

| Update Type | Frequency | Trigger | Entities/Operation | Notes |
|-------------|-----------|---------|-------------------|-------|
| **New Workloads** | Continuous | User submissions | 10-80 workloads/sec | Varies by time of day, bursty |
| **Workload Scheduling** | Continuous | Matcher assigns to node | 10-80 workloads/sec | status: pending→running, assigned_node |
| **Workload Completion** | Continuous | Job finishes | 10-80 workloads/sec | status: running→completed, resource release |
| **Workload Failures** | Occasional | Timeout, OOM, crashes | 0.5-10 workloads/sec | status: running→failed, retry_count++ |
| **Resource Updates** | Conditional | Usage change >20% | ~50-100 workloads/sec | Event-driven: actual_cpu, actual_mem |

**Workload Lifecycle:**

```
pending (new) → running (scheduled) → completed/failed (done)
              ↓                      ↓
         2 attrs update        2-3 attrs update
```

**Write Load Calculation:**

```
New submissions:       20 workloads/sec (create)
Scheduling (assign):   20 workloads/sec (update)
Completions:           20 workloads/sec (update)
Failures:              2 workloads/sec (update)
Resource updates:      100-200 workloads/sec (conditional: usage change >20%)
                       ─────────────────────────────────────────
Total steady-state:    ~160-260 workload entities/sec
```

**Resource Update Strategy:** Similar to health metrics, workloads check their actual resource usage every minute. Updates are only written when usage changes substantially (>20% deviation from last reported values). This captures meaningful billing/SLA events while avoiding redundant writes for stable workloads.

**Peak scenarios:**
- **Batch job submission**: Constrained by block capacity (2-sec blocks) = **~200 workloads/sec** (create)
- **Mass completion** (batch done): 3,000 workloads / 10 sec = **300 workloads/sec** (update)
- **Resource usage spike**: Many workloads change usage simultaneously = **~1,000 workloads/sec** (brief)

### 1.3 Combined Write Load

| Scenario | Node Updates | Workload Updates | Total | Peak Duration |
|----------|--------------|------------------|-------|---------------|
| **Steady-state** | 54-69 entities/sec | 115-165 entities/sec | **~170-235 entities/sec** | Continuous |
| **Batch submission** | 54-69 entities/sec | 200 entities/sec | **~255-270 entities/sec** | Block rate limited |
| **Resource usage spike** | 54-69 entities/sec | 1,000 entities/sec | **~1,050-1,070 entities/sec** | Brief, occasional |
| **Mass node failure** | 300 entities/sec | 115-165 entities/sec | **~415-465 entities/sec** | <10 sec, rare |
| **Mass job completion** | 54-69 entities/sec | 300 entities/sec | **~355-370 entities/sec** | ~10 sec, occasional |

**Design Target:**
- **Sustained**: 250 entities/sec (accounts for steady-state + normal bursts)
- **Peak**: 1,600 entities/sec (handles resource usage spikes + mass failures)
- **Batch size**: 100-500 entity updates per transaction

## 2. Read Performance Requirements

### 2.1 Problem Statement

**Core Challenge:** Match 500K pending workloads to 30K available nodes efficiently in a 100K node compute marketplace.

**Key Constraints:**
- **Matching pool**: 30K available nodes (65K busy, 5K offline)
- **Workload throughput**: 5.56 workloads/sec = 20K/hour
- **Fairness requirement**: Oldest workloads must be prioritized
- **Resource efficiency**: Idle nodes should not remain unused while workloads wait

### 2.2 Design Goals

1. **Minimize query count**: Avoid per-workload queries (5.6 queries/sec), use batch approach
2. **Avoid ORDER BY where possible**: Use filters with price ceilings instead of sorting large result sets
3. **Maximize resource utilization**: Continue matching from younger age buckets while nodes available
4. **Query performance**: Keep individual queries fast with LIMIT constraints
5. **Fair queuing**: Process workloads by age priority (oldest first)

### 2.3 Resource-Driven Age-Based Bucket Strategy

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
     - Result: Fetch only nodes that could match this batch (~1K-5K nodes)
   - **Step 4: Match in-memory**: Pair workloads to nodes
   - **Step 5: Update Entities** Batch update matched workloads/nodes
   - **Step 6: try to exhaust current workload bucket**: Go back to Step 1 as long as there are unmatched workloads and available nodes.
2. **Move to next bucket**: While not working on youngest bucket

**Key Features:**
- **Age-based priority**: Always starts with oldest bucket
- **Data-driven node queries**: Only fetch nodes matching current workload batch requirements
- **Per-query limits**: 100 workloads per query, only relevant nodes fetched
- **Unlimited matches per cycle**: Can match hundreds/thousands of workloads if nodes available
- **Fair queuing**: Older workloads never starve

### 2.4 Query Estimates

**Per Cycle (10 seconds):**

- Matches per cycle: 0-60K
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
| **Health monitoring** | 20 | Node status checks |
| **Tenant accounting** | 40 | Quota checks, billing |
| **Dashboard/reporting** | 20-60 | UI queries, analytics (cached, periodic refresh) |
| **Total steady-state** | **~120-240 queries/sec** | Normal operations |

**Peak Scenarios:**
- **Scheduler burst**: 10x matching rate = **200 queries/sec** (matching queries)
- **Monitoring sweep**: **1,500 queries/sec** (periodic health checks across fleet)
- **Design target**: **1,500 queries/sec sustained** | Realistic with headroom

### 2.5 Combined Read/Write Load

| Scenario | Write (entities/sec) | Read (queries/sec) | Notes |
|----------|---------------------|-------------------|-------|
| **Steady-state** | 170-235 | 120-240 | Balanced marketplace, conditional updates |
| **Resource usage spike** | 1,050-1,070 | 120-240 | Many workloads change usage simultaneously |
| **Batch submission** | 255-270 | 200 | Block rate limited (2-sec blocks) |
| **Node failure** | 415-465 | 200 | Decentralized: correlated failures rare |
| **Mass job completion** | 355-370 | 200 | Large batch finishes |
| **Design target** | **1,600 peak** | **1,500 sustained** | Realistic with headroom |

## 4. Performance Framework Summary

### Write Requirements

| Metric | Target | Peak | Notes |
|--------|--------|------|-------|
| **Sustained writes** | 1,000 entities/sec | 10,000 entities/sec | Handles steady-state + bursts |
| **Transaction size** | 1,000-5,000 entities | — | Batch for efficiency |
| **Commit latency** | <100ms | <500ms | p99 target |
| **Write availability** | 99.9% | — | Single writer, WAL mode |

### Read Requirements

| Metric | Target | Peak | Notes |
|--------|--------|------|-------|
| **Sustained reads** | 1,000 queries/sec | 2,500 queries/sec | Scheduler + monitoring |
| **Query latency (p50)** | <10ms | — | Point lookups, indexed filters |
| **Query latency (p99)** | <100ms | <500ms | Complex multi-constraint filters |
| **Result set size** | 10-100 rows | 1,000 rows max | Application-side final selection |
| **Read availability** | 99.99% | — | WAL allows concurrent reads |

### Capacity Validation

| Metric | 300K Node Marketplace | Mendoza Benchmark | Ratio | Status |
|--------|----------------------|-------------------|-------|--------|
| **Entities** | 663K | 800K | 0.83x | ✅ Within capacity |
| **Attributes** | 15.3M | 19.4M | 0.79x | ✅ Within capacity |
| **Write throughput** | 250 entities/sec | ~700 entities/sec | 0.36x | ✅ Comfortable margin |
| **Write peak** | 1.6K entities/sec | Unknown | ~2.3x | ⚠️ Needs benchmarking |
| **Read throughput** | 1.5K queries/sec | — | — | ⚠️ Needs benchmarking |
| **Read peak** | 1.5K queries/sec | — | — | ⚠️ Needs benchmarking |

**Confidence Level:**
- ✅ **Entity/attribute scale**: High confidence (within Mendoza proven capacity)
- ⚠️ **Write throughput**: Medium confidence (1.4x Mendoza, needs batching validation)
- ⚠️ **Write peak**: Low confidence (14x Mendoza, needs stress testing)
- ✅ **Read throughput**: High confidence (SQLite excels at reads with proper indexes)

## 5. Next Steps for Validation

1. **Benchmark batch writes**: Test 1K-10K entities/sec with realistic transaction sizes (1K-5K entities/tx)
2. **Index optimization**: Validate composite indexes for common query patterns
3. **Peak load simulation**: Stress test with resource metric windows (8K entities/sec for 30 sec bursts)
4. **Query plan analysis**: EXPLAIN QUERY PLAN for all common query types
5. **Concurrent read testing**: Validate 2.5K queries/sec with WAL mode

---

## Performance Assumptions

Based on experiments documented in [`experiments.md`](./experiments.md):

- **Entity throughput**: ~700 entities/sec (typical 5KB payload, 5 string + 3 numeric attrs)
- **Row throughput**: ~6,400 rows/sec
- **Key bottleneck**: String attribute count (correlation 0.95 with commit time)
- **Payload impact**: Minimal for current-state management (no historical logging)

---
