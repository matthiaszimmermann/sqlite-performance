"""
Query Performance Benchmark for Data Center databases.

Measures read query performance with a configurable mix of query types.

Usage:
    # Run benchmark with default mix (1000 queries)
    uv run python -m src.db.query_dc_benchmark \
        --database data/dc_seed_2x.db \
        --queries 1000

    # Specify current block (for bi-temporal queries)
    uv run python -m src.db.query_dc_benchmark \
        --database data/dc_seed_2x.db \
        --current-block 500 \
        --queries 5000
"""

import argparse
import csv
import json
import os
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, TextIO


# =============================================================================
# Constants
# =============================================================================

# Default query mix weights (must sum to 1.0)
QUERY_MIX = {
    "point_by_id": 0.20,       # 20% - Point lookup by node_id/workload_id
    "point_by_key": 0.15,      # 15% - Direct entity_key lookup
    "point_miss": 0.10,        # 10% - Non-existent entity lookup
    "node_filter": 0.25,       # 25% - Filter available nodes
    "workload_simple": 0.15,   # 15% - Find pending workloads
    "workload_specific": 0.15, # 15% - Find pending workloads with filters
}

# Memory allocation for SQLite in GB
DEFAULT_MEMORY_GB = 16

# Sample sizes for pre-loading IDs
SAMPLE_SIZE_IDS = 1000
SAMPLE_SIZE_KEYS = 1000

# Regions and VM types for filter queries
REGIONS = ["eu-west", "us-east", "asia-pac"]
VM_TYPES = ["cpu", "gpu", "gpu_large"]

# Default result set limits
DEFAULT_NODE_LIMIT = 100
DEFAULT_WORKLOAD_LIMIT = 100


class QueryType(Enum):
    """Query type identifiers."""
    POINT_BY_ID = "point_by_id"
    POINT_BY_KEY = "point_by_key"
    POINT_MISS = "point_miss"
    NODE_FILTER = "node_filter"
    WORKLOAD_SIMPLE = "workload_simple"
    WORKLOAD_SPECIFIC = "workload_specific"


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class QueryParams:
    """Parameters for a single query."""
    current_block: int
    entity_id: str | None = None        # node_id or workload_id
    entity_key: bytes | None = None     # Direct key lookup
    region: str | None = None
    vm_type: str | None = None
    min_cpu: int | None = None
    min_ram: int | None = None
    min_hours: int | None = None
    max_price: int | None = None


@dataclass
class QueryResult:
    """Result of a single query."""
    query_type: QueryType
    latency_ms: float
    row_count: int
    success: bool
    error: str | None = None


# =============================================================================
# Query Generator
# =============================================================================

class QueryGenerator:
    """Generates query parameters for benchmark."""
    
    def __init__(self, conn: sqlite3.Connection, current_block: int, seed: int | None = None):
        self.conn = conn
        self.current_block = current_block
        self.rng = random.Random(seed) if seed else random.Random()
        
        # Pre-load sample data
        self._node_ids: list[str] = []
        self._workload_ids: list[str] = []
        self._entity_keys: list[bytes] = []
        self._load_sample_ids()
        self._load_sample_keys()
    
    def _load_sample_ids(self) -> None:
        """Pre-load valid node_ids and workload_ids for point queries."""
        cursor = self.conn.cursor()
        
        # Load node IDs
        cursor.execute("""
            SELECT DISTINCT value FROM string_attributes
            WHERE key = 'node_id'
              AND from_block <= ? AND to_block > ?
            ORDER BY RANDOM()
            LIMIT ?
        """, (self.current_block, self.current_block, SAMPLE_SIZE_IDS))
        self._node_ids = [row[0] for row in cursor.fetchall()]
        
        # Load workload IDs
        cursor.execute("""
            SELECT DISTINCT value FROM string_attributes
            WHERE key = 'workload_id'
              AND from_block <= ? AND to_block > ?
            ORDER BY RANDOM()
            LIMIT ?
        """, (self.current_block, self.current_block, SAMPLE_SIZE_IDS))
        self._workload_ids = [row[0] for row in cursor.fetchall()]
        
        print(f"Loaded {len(self._node_ids)} node IDs, {len(self._workload_ids)} workload IDs")
    
    def _load_sample_keys(self) -> None:
        """Pre-load valid entity_keys for direct lookups."""
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT entity_key FROM payloads
            WHERE from_block <= ? AND to_block > ?
            ORDER BY RANDOM()
            LIMIT ?
        """, (self.current_block, self.current_block, SAMPLE_SIZE_KEYS))
        self._entity_keys = [row[0] for row in cursor.fetchall()]
        
        print(f"Loaded {len(self._entity_keys)} entity keys")
    
    def generate_random_uuid(self) -> str:
        """Generate a random UUID that doesn't exist in the database."""
        return f"node_{uuid.uuid4().hex[:12]}"
    
    def generate_params(self, query_type: QueryType) -> QueryParams:
        """Generate parameters for a specific query type."""
        params = QueryParams(current_block=self.current_block)
        
        if query_type == QueryType.POINT_BY_ID:
            # Randomly choose node or workload ID
            if self.rng.random() < 0.5 and self._node_ids:
                params.entity_id = self.rng.choice(self._node_ids)
            elif self._workload_ids:
                params.entity_id = self.rng.choice(self._workload_ids)
            elif self._node_ids:
                params.entity_id = self.rng.choice(self._node_ids)
        
        elif query_type == QueryType.POINT_BY_KEY:
            if self._entity_keys:
                params.entity_key = self.rng.choice(self._entity_keys)
        
        elif query_type == QueryType.POINT_MISS:
            params.entity_id = self.generate_random_uuid()
        
        elif query_type == QueryType.NODE_FILTER:
            params.region = self.rng.choice(REGIONS)
            params.vm_type = self.rng.choice(VM_TYPES)
            params.min_cpu = self.rng.choice([1, 2, 4, 8])
            params.min_ram = self.rng.choice([4, 8, 16, 32])
            params.min_hours = self.rng.choice([1, 4, 8])
            params.max_price = self.rng.randint(100, 400)
        
        elif query_type == QueryType.WORKLOAD_SIMPLE:
            pass  # No additional params needed
        
        elif query_type == QueryType.WORKLOAD_SPECIFIC:
            params.region = self.rng.choice(REGIONS)
            params.vm_type = self.rng.choice(VM_TYPES)
        
        return params


# =============================================================================
# Query Executor
# =============================================================================

class QueryExecutor:
    """Executes queries and measures latency."""
    
    def __init__(
        self,
        conn: sqlite3.Connection,
        current_block: int,
        log_file: TextIO | None = None,
        node_limit: int = DEFAULT_NODE_LIMIT,
        workload_limit: int = DEFAULT_WORKLOAD_LIMIT,
    ):
        self.conn = conn
        self.current_block = current_block
        self.log_file = log_file
        self.node_limit = node_limit
        self.workload_limit = workload_limit
        self.csv_writer: csv.writer | None = None
        if log_file:
            self.csv_writer = csv.writer(log_file)
            # Write header
            self.csv_writer.writerow(["timestamp", "query_type", "latency_ms", "row_count", "params"])
    
    def _log_query(self, query_type: QueryType, result: QueryResult, params: QueryParams) -> None:
        """Log query execution to CSV file."""
        if self.csv_writer:
            # Convert params to dict, excluding None values
            params_dict = {k: v for k, v in asdict(params).items() if v is not None}
            # Convert entity_key bytes to hex if present
            if "entity_key" in params_dict and params_dict["entity_key"]:
                params_dict["entity_key"] = params_dict["entity_key"].hex()
            self.csv_writer.writerow([
                datetime.now().isoformat(),
                query_type.value,
                f"{result.latency_ms:.3f}",
                result.row_count,
                json.dumps(params_dict)
            ])
    
    def execute(self, query_type: QueryType, params: QueryParams) -> QueryResult:
        """Execute a query and return the result with timing."""
        result: QueryResult
        try:
            if query_type == QueryType.POINT_BY_ID:
                result = self._execute_point_by_id(params)
            elif query_type == QueryType.POINT_BY_KEY:
                result = self._execute_point_by_key(params)
            elif query_type == QueryType.POINT_MISS:
                result = self._execute_point_miss(params)
            elif query_type == QueryType.NODE_FILTER:
                result = self._execute_node_filter(params)
            elif query_type == QueryType.WORKLOAD_SIMPLE:
                result = self._execute_workload_simple(params)
            elif query_type == QueryType.WORKLOAD_SPECIFIC:
                result = self._execute_workload_specific(params)
            else:
                result = QueryResult(
                    query_type=query_type,
                    latency_ms=0,
                    row_count=0,
                    success=False,
                    error=f"Unknown query type: {query_type}"
                )
        except Exception as e:
            result = QueryResult(
                query_type=query_type,
                latency_ms=0,
                row_count=0,
                success=False,
                error=str(e)
            )
        
        # Log to CSV if enabled
        self._log_query(query_type, result, params)
        return result
    
    def _execute_point_by_id(self, params: QueryParams) -> QueryResult:
        """Point lookup by node_id or workload_id."""
        start = time.perf_counter()
        cursor = self.conn.cursor()
        
        # Determine if it's a node or workload ID
        id_key = "node_id" if params.entity_id and params.entity_id.startswith("node_") else "workload_id"
        
        # Step 1: Get entity_key from ID
        cursor.execute("""
            SELECT entity_key FROM string_attributes
            WHERE key = ? AND value = ?
              AND from_block <= ? AND to_block > ?
            ORDER BY from_block DESC
            LIMIT 1
        """, (id_key, params.entity_id, params.current_block, params.current_block))
        
        row = cursor.fetchone()
        if not row:
            latency_ms = (time.perf_counter() - start) * 1000
            return QueryResult(
                query_type=QueryType.POINT_BY_ID,
                latency_ms=latency_ms,
                row_count=0,
                success=True
            )
        
        entity_key = row[0]
        
        # Step 2: Get all attributes
        cursor.execute("""
            SELECT key, value FROM string_attributes
            WHERE entity_key = ?
              AND from_block <= ? AND to_block > ?
        """, (entity_key, params.current_block, params.current_block))
        str_attrs = cursor.fetchall()
        
        cursor.execute("""
            SELECT key, value FROM numeric_attributes
            WHERE entity_key = ?
              AND from_block <= ? AND to_block > ?
        """, (entity_key, params.current_block, params.current_block))
        num_attrs = cursor.fetchall()
        
        cursor.execute("""
            SELECT payload FROM payloads
            WHERE entity_key = ?
              AND from_block <= ? AND to_block > ?
            ORDER BY from_block DESC
            LIMIT 1
        """, (entity_key, params.current_block, params.current_block))
        payload = cursor.fetchone()
        
        latency_ms = (time.perf_counter() - start) * 1000
        row_count = len(str_attrs) + len(num_attrs) + (1 if payload else 0)
        
        return QueryResult(
            query_type=QueryType.POINT_BY_ID,
            latency_ms=latency_ms,
            row_count=row_count,
            success=True
        )
    
    def _execute_point_by_key(self, params: QueryParams) -> QueryResult:
        """Direct lookup by entity_key."""
        start = time.perf_counter()
        cursor = self.conn.cursor()
        
        if not params.entity_key:
            return QueryResult(
                query_type=QueryType.POINT_BY_KEY,
                latency_ms=0,
                row_count=0,
                success=False,
                error="No entity_key provided"
            )
        
        # Get all attributes directly
        cursor.execute("""
            SELECT key, value FROM string_attributes
            WHERE entity_key = ?
              AND from_block <= ? AND to_block > ?
        """, (params.entity_key, params.current_block, params.current_block))
        str_attrs = cursor.fetchall()
        
        cursor.execute("""
            SELECT key, value FROM numeric_attributes
            WHERE entity_key = ?
              AND from_block <= ? AND to_block > ?
        """, (params.entity_key, params.current_block, params.current_block))
        num_attrs = cursor.fetchall()
        
        cursor.execute("""
            SELECT payload FROM payloads
            WHERE entity_key = ?
              AND from_block <= ? AND to_block > ?
            ORDER BY from_block DESC
            LIMIT 1
        """, (params.entity_key, params.current_block, params.current_block))
        payload = cursor.fetchone()
        
        latency_ms = (time.perf_counter() - start) * 1000
        row_count = len(str_attrs) + len(num_attrs) + (1 if payload else 0)
        
        return QueryResult(
            query_type=QueryType.POINT_BY_KEY,
            latency_ms=latency_ms,
            row_count=row_count,
            success=True
        )
    
    def _execute_point_miss(self, params: QueryParams) -> QueryResult:
        """Lookup non-existent entity (guaranteed miss)."""
        start = time.perf_counter()
        cursor = self.conn.cursor()
        
        # Try to find by random UUID (should return 0 rows)
        cursor.execute("""
            SELECT entity_key FROM string_attributes
            WHERE key = 'node_id' AND value = ?
              AND from_block <= ? AND to_block > ?
            LIMIT 1
        """, (params.entity_id, params.current_block, params.current_block))
        
        row = cursor.fetchone()
        latency_ms = (time.perf_counter() - start) * 1000
        
        return QueryResult(
            query_type=QueryType.POINT_MISS,
            latency_ms=latency_ms,
            row_count=0 if not row else 1,
            success=True
        )
    
    def _execute_node_filter(self, params: QueryParams) -> QueryResult:
        """Find available nodes matching filter criteria."""
        start = time.perf_counter()
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT sa_status.entity_key
            FROM string_attributes sa_status
            JOIN string_attributes sa_region 
                ON sa_status.entity_key = sa_region.entity_key
            JOIN string_attributes sa_vm 
                ON sa_status.entity_key = sa_vm.entity_key
            JOIN numeric_attributes na_cpu 
                ON sa_status.entity_key = na_cpu.entity_key
            JOIN numeric_attributes na_ram 
                ON sa_status.entity_key = na_ram.entity_key
            JOIN numeric_attributes na_hours 
                ON sa_status.entity_key = na_hours.entity_key
            JOIN numeric_attributes na_price 
                ON sa_status.entity_key = na_price.entity_key
            WHERE sa_status.key = 'status' AND sa_status.value = 'available'
              AND sa_region.key = 'region' AND sa_region.value = ?
              AND sa_vm.key = 'vm_type' AND sa_vm.value = ?
              AND na_cpu.key = 'cpu_count' AND na_cpu.value >= ?
              AND na_ram.key = 'ram_gb' AND na_ram.value >= ?
              AND na_hours.key = 'avail_hours' AND na_hours.value >= ?
              AND na_price.key = 'price_hour' AND na_price.value <= ?
              AND sa_status.from_block <= ? AND sa_status.to_block > ?
              AND sa_region.from_block <= ? AND sa_region.to_block > ?
              AND sa_vm.from_block <= ? AND sa_vm.to_block > ?
              AND na_cpu.from_block <= ? AND na_cpu.to_block > ?
              AND na_ram.from_block <= ? AND na_ram.to_block > ?
              AND na_hours.from_block <= ? AND na_hours.to_block > ?
              AND na_price.from_block <= ? AND na_price.to_block > ?
            ORDER BY na_price.value ASC
            LIMIT ?
        """, (
            params.region, params.vm_type,
            params.min_cpu, params.min_ram, params.min_hours, params.max_price,
            params.current_block, params.current_block,  # sa_status
            params.current_block, params.current_block,  # sa_region
            params.current_block, params.current_block,  # sa_vm
            params.current_block, params.current_block,  # na_cpu
            params.current_block, params.current_block,  # na_ram
            params.current_block, params.current_block,  # na_hours
            params.current_block, params.current_block,  # na_price
            self.node_limit,
        ))
        
        rows = cursor.fetchall()
        latency_ms = (time.perf_counter() - start) * 1000
        
        return QueryResult(
            query_type=QueryType.NODE_FILTER,
            latency_ms=latency_ms,
            row_count=len(rows),
            success=True
        )
    
    def _execute_workload_simple(self, params: QueryParams) -> QueryResult:
        """Find pending workloads (status filter only)."""
        start = time.perf_counter()
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT sa.entity_key
            FROM string_attributes sa
            WHERE sa.key = 'status' AND sa.value = 'pending'
              AND sa.from_block <= ? AND sa.to_block > ?
              AND EXISTS (
                SELECT 1 FROM string_attributes sa2
                WHERE sa2.entity_key = sa.entity_key
                  AND sa2.key = 'type' AND sa2.value = 'workload'
                  AND sa2.from_block <= ? AND sa2.to_block > ?
              )
            LIMIT ?
        """, (
            params.current_block, params.current_block,
            params.current_block, params.current_block,
            self.workload_limit,
        ))
        
        rows = cursor.fetchall()
        latency_ms = (time.perf_counter() - start) * 1000
        
        return QueryResult(
            query_type=QueryType.WORKLOAD_SIMPLE,
            latency_ms=latency_ms,
            row_count=len(rows),
            success=True
        )
    
    def _execute_workload_specific(self, params: QueryParams) -> QueryResult:
        """Find pending workloads matching region and vm_type."""
        start = time.perf_counter()
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT sa_status.entity_key
            FROM string_attributes sa_status
            JOIN string_attributes sa_region 
                ON sa_status.entity_key = sa_region.entity_key
            JOIN string_attributes sa_vm 
                ON sa_status.entity_key = sa_vm.entity_key
            WHERE sa_status.key = 'status' AND sa_status.value = 'pending'
              AND sa_region.key = 'region' AND sa_region.value = ?
              AND sa_vm.key = 'vm_type' AND sa_vm.value = ?
              AND sa_status.from_block <= ? AND sa_status.to_block > ?
              AND sa_region.from_block <= ? AND sa_region.to_block > ?
              AND sa_vm.from_block <= ? AND sa_vm.to_block > ?
              AND EXISTS (
                SELECT 1 FROM string_attributes sa2
                WHERE sa2.entity_key = sa_status.entity_key
                  AND sa2.key = 'type' AND sa2.value = 'workload'
                  AND sa2.from_block <= ? AND sa2.to_block > ?
              )
            LIMIT ?
        """, (
            params.region, params.vm_type,
            params.current_block, params.current_block,  # sa_status
            params.current_block, params.current_block,  # sa_region
            params.current_block, params.current_block,  # sa_vm
            params.current_block, params.current_block,  # EXISTS subquery
            self.workload_limit,
        ))
        
        rows = cursor.fetchall()
        latency_ms = (time.perf_counter() - start) * 1000
        
        return QueryResult(
            query_type=QueryType.WORKLOAD_SPECIFIC,
            latency_ms=latency_ms,
            row_count=len(rows),
            success=True
        )


# =============================================================================
# Benchmark Runner
# =============================================================================

class BenchmarkRunner:
    """Orchestrates the benchmark execution."""
    
    def __init__(
        self,
        conn: sqlite3.Connection,
        generator: QueryGenerator,
        executor: QueryExecutor,
        query_mix: dict[str, float],
    ):
        self.conn = conn
        self.generator = generator
        self.executor = executor
        self.query_mix = query_mix
        self._query_types = list(QueryType)
        self._weights = [query_mix.get(qt.value, 0) for qt in self._query_types]
    
    def _select_query_type(self) -> QueryType:
        """Select a query type based on weighted random selection."""
        return self.generator.rng.choices(self._query_types, weights=self._weights, k=1)[0]
    
    def run(self, num_queries: int, warmup: int = 100) -> list[QueryResult]:
        """Run the benchmark and return results."""
        results: list[QueryResult] = []
        
        # Warmup phase
        if warmup > 0:
            print(f"Running {warmup} warmup queries...")
            for _ in range(warmup):
                query_type = self._select_query_type()
                params = self.generator.generate_params(query_type)
                self.executor.execute(query_type, params)
        
        # Benchmark phase
        print(f"Running {num_queries} benchmark queries...")
        start_time = time.time()
        
        for i in range(num_queries):
            query_type = self._select_query_type()
            params = self.generator.generate_params(query_type)
            result = self.executor.execute(query_type, params)
            results.append(result)
            
            if (i + 1) % 1000 == 0:
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed
                print(f"  Progress: {i + 1:,}/{num_queries:,} ({rate:.0f} queries/sec)")
        
        return results
    
    @staticmethod
    def compute_statistics(results: list[QueryResult]) -> dict[str, Any]:
        """Compute statistics from benchmark results."""
        stats: dict[str, Any] = {
            "total_queries": len(results),
            "successful_queries": sum(1 for r in results if r.success),
            "failed_queries": sum(1 for r in results if not r.success),
            "by_type": {},
        }
        
        # Group by query type
        by_type: dict[QueryType, list[float]] = {}
        by_type_rows: dict[QueryType, list[int]] = {}
        for result in results:
            if result.success:
                if result.query_type not in by_type:
                    by_type[result.query_type] = []
                    by_type_rows[result.query_type] = []
                by_type[result.query_type].append(result.latency_ms)
                by_type_rows[result.query_type].append(result.row_count)
        
        # Compute percentiles for each type
        for query_type, latencies in by_type.items():
            if latencies:
                latencies.sort()
                n = len(latencies)
                row_counts = by_type_rows[query_type]
                stats["by_type"][query_type.value] = {
                    "count": n,
                    "avg_rows": sum(row_counts) / n if row_counts else 0,
                    "max_rows": max(row_counts) if row_counts else 0,
                    "p50": latencies[int(n * 0.50)],
                    "p95": latencies[int(n * 0.95)] if n > 1 else latencies[0],
                    "p99": latencies[int(n * 0.99)] if n > 1 else latencies[0],
                    "max": latencies[-1],
                    "avg": sum(latencies) / n,
                }
        
        # Overall statistics
        all_latencies = [r.latency_ms for r in results if r.success]
        all_row_counts = [r.row_count for r in results if r.success]
        if all_latencies:
            all_latencies.sort()
            n = len(all_latencies)
            stats["overall"] = {
                "p50": all_latencies[int(n * 0.50)],
                "p95": all_latencies[int(n * 0.95)] if n > 1 else all_latencies[0],
                "p99": all_latencies[int(n * 0.99)] if n > 1 else all_latencies[0],
                "max": all_latencies[-1],
                "avg": sum(all_latencies) / n,
                "total_time_ms": sum(all_latencies),
                "avg_row_count": sum(all_row_counts) / n if all_row_counts else 0,
            }
        
        return stats


# =============================================================================
# Reporter
# =============================================================================

class Reporter:
    """Formats and prints benchmark results."""
    
    @staticmethod
    def print_report(stats: dict[str, Any], config: dict[str, Any]) -> None:
        """Print formatted benchmark report."""
        print()
        print("=" * 60)
        print("Query Benchmark Results")
        print("=" * 60)
        print(f"Database:           {config['database']}")
        print(f"Current block:      {config['current_block']:,}")
        print(f"Total queries:      {stats['total_queries']:,}")
        print(f"Successful:         {stats['successful_queries']:,}")
        print(f"Failed:             {stats['failed_queries']:,}")
        print(f"Warmup queries:     {config['warmup']:,}")
        print()
        
        # Latency table
        print("--- Latency (ms) ---")
        print(f"{'Query Type':<20} {'Count':>7} {'Rows':>7} {'MaxRows':>7} {'p50':>8} {'p95':>8} {'p99':>8} {'max':>8}")
        print("-" * 80)
        
        for query_type in QueryType:
            type_stats = stats["by_type"].get(query_type.value)
            if type_stats:
                print(f"{query_type.value:<20} {type_stats['count']:>7} "
                      f"{type_stats['avg_rows']:>7.1f} {type_stats['max_rows']:>7} "
                      f"{type_stats['p50']:>8.2f} {type_stats['p95']:>8.2f} "
                      f"{type_stats['p99']:>8.2f} {type_stats['max']:>8.2f}")
        
        print("-" * 80)
        
        if "overall" in stats:
            overall = stats["overall"]
            print(f"{'OVERALL':<20} {stats['successful_queries']:>7} "
                  f"{overall['avg_row_count']:>7.1f} {'':>7} "
                  f"{overall['p50']:>8.2f} {overall['p95']:>8.2f} "
                  f"{overall['p99']:>8.2f} {overall['max']:>8.2f}")
        
        print()
        
        # Throughput
        if "overall" in stats:
            total_time_s = stats["overall"]["total_time_ms"] / 1000
            queries_per_sec = stats["successful_queries"] / total_time_s if total_time_s > 0 else 0
            print("--- Throughput ---")
            print(f"Total query time:   {total_time_s:.2f}s")
            print(f"Queries/sec:        {queries_per_sec:.1f}")
            print(f"Avg latency:        {stats['overall']['avg']:.2f}ms")
            print(f"Avg result set:     {stats['overall']['avg_row_count']:.1f} rows")
        
        print()
        
        # Query distribution
        print("--- Query Distribution ---")
        for query_type in QueryType:
            type_stats = stats["by_type"].get(query_type.value)
            if type_stats:
                pct = (type_stats["count"] / stats["successful_queries"]) * 100
                print(f"{query_type.value:<20} {type_stats['count']:>8} ({pct:>5.1f}%)")
        
        print("=" * 60)


# =============================================================================
# Database Configuration
# =============================================================================

def configure_connection(conn: sqlite3.Connection, memory_gb: int) -> None:
    """Configure SQLite connection for optimal read performance."""
    # For read-only workloads: small cache, large mmap
    cache_mb = 256
    mmap_gb = memory_gb - 1
    
    cache_kb = cache_mb * 1024
    conn.execute(f"PRAGMA cache_size = -{cache_kb}")
    
    mmap_bytes = mmap_gb * 1024 * 1024 * 1024
    conn.execute(f"PRAGMA mmap_size = {mmap_bytes}")
    
    conn.execute("PRAGMA temp_store = MEMORY")
    
    print(f"Memory config: {cache_mb}MB cache, {mmap_gb}GB mmap")


def get_current_block(conn: sqlite3.Connection) -> int:
    """Get current block from database."""
    cursor = conn.cursor()
    cursor.execute("SELECT block FROM last_block WHERE id = 1")
    row = cursor.fetchone()
    if row:
        return row[0]
    
    # Fallback: get max from_block
    cursor.execute("SELECT MAX(from_block) FROM string_attributes")
    row = cursor.fetchone()
    return row[0] if row and row[0] else 1


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Query Performance Benchmark for Data Center databases"
    )
    parser.add_argument(
        "--database", "-d",
        type=str,
        required=True,
        help="Path to database file"
    )
    parser.add_argument(
        "--queries", "-q",
        type=int,
        default=1000,
        help="Total number of queries to execute (default: 1000)"
    )
    parser.add_argument(
        "--current-block",
        type=int,
        default=None,
        help="Block number for bi-temporal queries (default: from DB)"
    )
    parser.add_argument(
        "--mix",
        type=str,
        default=None,
        help="JSON object with query type weights"
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=100,
        help="Number of warmup queries (default: 100)"
    )
    parser.add_argument(
        "--memory", "-m",
        type=int,
        default=DEFAULT_MEMORY_GB,
        help=f"Memory allocation in GB for SQLite (default: {DEFAULT_MEMORY_GB})"
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=None,
        help="Random seed for reproducibility"
    )
    parser.add_argument(
        "--log", "-l",
        type=str,
        default=None,
        help="Path to CSV log file for query details (e.g., benchmark.log)"
    )
    parser.add_argument(
        "--node-limit",
        type=int,
        default=DEFAULT_NODE_LIMIT,
        help=f"Max result set size for node filter queries (default: {DEFAULT_NODE_LIMIT})"
    )
    parser.add_argument(
        "--workload-limit",
        type=int,
        default=DEFAULT_WORKLOAD_LIMIT,
        help=f"Max result set size for workload filter queries (default: {DEFAULT_WORKLOAD_LIMIT})"
    )
    
    args = parser.parse_args()
    
    # Validate database exists
    if not os.path.exists(args.database):
        print(f"Error: Database not found: {args.database}")
        return 1
    
    # Parse query mix
    query_mix = QUERY_MIX.copy()
    if args.mix:
        try:
            custom_mix = json.loads(args.mix)
            query_mix.update(custom_mix)
        except json.JSONDecodeError as e:
            print(f"Error parsing --mix JSON: {e}")
            return 1
    
    # Normalize weights
    total_weight = sum(query_mix.values())
    if total_weight > 0:
        query_mix = {k: v / total_weight for k, v in query_mix.items()}
    
    print("=" * 60)
    print("Query Benchmark")
    print("=" * 60)
    print(f"Database:           {args.database}")
    print(f"Database size:      {os.path.getsize(args.database) / (1024**3):.2f} GB")
    print(f"Queries:            {args.queries:,}")
    print(f"Warmup:             {args.warmup:,}")
    print(f"Seed:               {args.seed or 'random'}")
    print(f"Log file:           {args.log or 'none'}")
    print(f"Node limit:         {args.node_limit}")
    print(f"Workload limit:     {args.workload_limit}")
    print()
    
    # Connect to database
    conn = sqlite3.connect(args.database)
    configure_connection(conn, args.memory)
    
    # Get current block
    current_block = args.current_block or get_current_block(conn)
    print(f"Current block:      {current_block:,}")
    print()
    
    # Open log file if specified
    log_file = open(args.log, "w", newline="") if args.log else None
    
    # Initialize components
    print("Initializing...")
    generator = QueryGenerator(conn, current_block, args.seed)
    executor = QueryExecutor(
        conn, current_block, log_file,
        node_limit=args.node_limit,
        workload_limit=args.workload_limit,
    )
    runner = BenchmarkRunner(conn, generator, executor, query_mix)
    
    # Check if we have enough sample data
    if not generator._node_ids and not generator._workload_ids:
        print("Warning: No valid entities found for point queries!")
    if not generator._entity_keys:
        print("Warning: No valid entity keys found for direct lookups!")
    
    print()
    
    # Run benchmark
    start_time = time.time()
    results = runner.run(args.queries, args.warmup)
    total_time = time.time() - start_time
    
    # Compute statistics
    stats = BenchmarkRunner.compute_statistics(results)
    
    # Print report
    config = {
        "database": args.database,
        "current_block": current_block,
        "warmup": args.warmup,
    }
    Reporter.print_report(stats, config)
    
    print(f"\nTotal benchmark time: {total_time:.1f}s")
    
    # Cleanup
    if log_file:
        log_file.close()
        print(f"Query log written to: {args.log}")
    
    conn.close()
    return 0


if __name__ == "__main__":
    exit(main())
