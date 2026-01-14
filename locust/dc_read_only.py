"""
Locust stress test for op-geth-simulator read/query endpoints.

This test performs read queries similar to query_dc_benchmark.py, using the same
query types and weights. Sample data (node_ids, workload_ids, entity_keys) is
pre-loaded globally once when the test starts.

The test uses range queries for numeric annotations (>=, <=, >, <, !=) where
appropriate, leveraging the extended queryEntities API that supports Arkiv query
language with comparison operators.

Usage:
    locust -f locust/read_only.py --host=http://localhost:3000
"""

import json
import os
import random
import sqlite3
import sys
from typing import Dict, Any, List, Optional
from urllib.parse import quote

from locust import constant, task, events
from locust.contrib.fasthttp import FastHttpUser

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# =============================================================================
# Configuration
# =============================================================================

# Logging level: DEBUG, INFO, WARNING, ERROR, or NONE (to disable all debug logs)
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()

# Query mix weights (must sum to 1.0)
QUERY_MIX = {
    "point_by_id": 0.20,       # 20% - Point lookup by node_id/workload_id
    "point_by_key": 0.15,      # 15% - Direct entity_key lookup
    "point_miss": 0.10,        # 10% - Non-existent entity lookup
    "node_filter": 0.25,       # 25% - Filter available nodes
    "workload_simple": 0.15,   # 15% - Find pending workloads
    "workload_specific": 0.15, # 15% - Find pending workloads with filters
}

# Sample sizes for pre-loading IDs
SAMPLE_SIZE_IDS = 1000
SAMPLE_SIZE_KEYS = 1000

# Regions and VM types for filter queries
REGIONS = ["eu-west", "us-east", "asia-pac"]
VM_TYPES = ["cpu", "gpu", "gpu_large"]

# Default result set limits
DEFAULT_NODE_LIMIT = 100
DEFAULT_WORKLOAD_LIMIT = 100

# Database path (can be set via environment variable)
DB_PATH = os.getenv("DC_DB_PATH", "data/dc_test.db")


# =============================================================================
# Logging Helper
# =============================================================================

def debug_log(message: str) -> None:
    """Print debug message if LOG_LEVEL is DEBUG."""
    if LOG_LEVEL == "DEBUG":
        print(message)


# =============================================================================
# Global Sample Data (loaded once at test start)
# =============================================================================

class GlobalSampleData:
    """Global sample data loaded once at test start."""
    
    node_ids: List[str] = []
    workload_ids: List[str] = []
    entity_keys: List[str] = []  # Stored as hex strings for API
    current_block: int = 1
    initialized: bool = False
    
    @classmethod
    def load_from_database(cls, db_path: str) -> None:
        """Load sample data from database."""
        if cls.initialized:
            return
        
        if not os.path.exists(db_path):
            print(f"Warning: Database not found at {db_path}, using empty sample data")
            cls.initialized = True
            return
        
        print(f"Loading sample data from {db_path}...")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Load node IDs from new schema (string_attributes_values_bitmaps)
        try:
            cursor.execute("""
                SELECT DISTINCT value
                FROM string_attributes_values_bitmaps
                WHERE name = 'node_id'
                ORDER BY RANDOM()
                LIMIT ?
            """, (SAMPLE_SIZE_IDS,))
            
            for row in cursor.fetchall():
                node_id = row[0]
                cls.node_ids.append(node_id)
        except Exception as e:
            print(f"Error loading node IDs: {e}")
        
        # Load workload IDs from new schema (string_attributes_values_bitmaps)
        try:
            cursor.execute("""
                SELECT DISTINCT value
                FROM string_attributes_values_bitmaps
                WHERE name = 'workload_id'
                ORDER BY RANDOM()
                LIMIT ?
            """, (SAMPLE_SIZE_IDS,))
            
            for row in cursor.fetchall():
                workload_id = row[0]
                cls.workload_ids.append(workload_id)
        except Exception as e:
            print(f"Error loading workload IDs: {e}")
        
        # Load entity keys (for direct lookups) - get single entity key as requested
        try:
            # Try the user's query first (with id column)
            try:
                cursor.execute("SELECT * FROM payloads ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                if row:
                    # Get entity_key from the row by column name
                    columns = [description[0] for description in cursor.description]
                    if 'entity_key' in columns:
                        entity_key_idx = columns.index('entity_key')
                        entity_key = row[entity_key_idx]
                    else:
                        # Fallback: assume entity_key is the first column
                        entity_key = row[0]
                    
                    if isinstance(entity_key, bytes):
                        key_hex = entity_key.hex()
                        cls.entity_keys.append(f"0x{key_hex}")
                    else:
                        key_str = str(entity_key)
                        if not key_str.startswith("0x"):
                            cls.entity_keys.append(f"0x{key_str}")
                        else:
                            cls.entity_keys.append(key_str)
            except sqlite3.OperationalError:
                # If id column doesn't exist, try with from_block
                cursor.execute("SELECT entity_key FROM payloads ORDER BY from_block DESC LIMIT 1")
                row = cursor.fetchone()
                if row:
                    entity_key = row[0]
                    if isinstance(entity_key, bytes):
                        key_hex = entity_key.hex()
                        cls.entity_keys.append(f"0x{key_hex}")
                    else:
                        key_str = str(entity_key)
                        if not key_str.startswith("0x"):
                            cls.entity_keys.append(f"0x{key_str}")
                        else:
                            cls.entity_keys.append(key_str)
        except Exception as e:
            print(f"Error loading entity keys: {e}")
        
        conn.close()
        
        print(f"Loaded {len(cls.node_ids)} node IDs, {len(cls.workload_ids)} workload IDs, "
              f"{len(cls.entity_keys)} entity keys (current block: {cls.current_block})")
        cls.initialized = True


# =============================================================================
# Locust User Class
# =============================================================================

class DataCenterReadUser(FastHttpUser):
    """
    Locust user that performs read queries on op-geth-simulator.
    
    Each user randomly selects query types based on QUERY_MIX weights.
    """
    wait_time = constant(1)
    
    def on_start(self):
        """Initialize user-specific state."""
        # Ensure global data is loaded
        if not GlobalSampleData.initialized:
            GlobalSampleData.load_from_database(DB_PATH)
    
    @task(20)  # 20% weight
    def point_by_id(self):
        """Point lookup by node_id or workload_id."""
        if not GlobalSampleData.node_ids and not GlobalSampleData.workload_ids:
            return
        
        # Randomly choose node or workload ID
        rng = random.Random()
        entity_id = None
        id_key = None
        
        if rng.random() < 0.5 and GlobalSampleData.node_ids:
            entity_id = rng.choice(GlobalSampleData.node_ids)
            id_key = "node_id"
        elif GlobalSampleData.workload_ids:
            entity_id = rng.choice(GlobalSampleData.workload_ids)
            id_key = "workload_id"
        elif GlobalSampleData.node_ids:
            entity_id = rng.choice(GlobalSampleData.node_ids)
            id_key = "node_id"
        
        if not entity_id:
            return
        
        query_body = {
            "stringAnnotations": {
                id_key: entity_id,
            },
        }
        
        debug_log(f"[DEBUG] point_by_id: querying {id_key}={entity_id}")
        
        # Use POST /entities/query endpoint
        with self.client.post("/entities/query", json=query_body, catch_response=True, name="point_by_id") as response:
            if response.status_code != 200:
                debug_log(f"[DEBUG] point_by_id: FAILED - status={response.status_code}, entity_id={entity_id}")
                response.failure(f"Unexpected status: {response.status_code}")
            else:
                try:
                    result = response.json()
                    count = result.get("count", 0)
                    debug_log(f"[DEBUG] point_by_id: SUCCESS - found {count} entities for {id_key}={entity_id}")
                except Exception:
                    debug_log(f"[DEBUG] point_by_id: SUCCESS - status=200 (could not parse response)")
            response.success()
    
    @task(15)  # 15% weight
    def point_by_key(self):
        """Direct lookup by entity_key."""
        if not GlobalSampleData.entity_keys:
            return
        
        # Use the single entity key (or first one if multiple somehow)
        entity_key = GlobalSampleData.entity_keys[0]
        debug_log(f"[DEBUG] point_by_key: querying entity_key={entity_key[:20]}...")
        
        # Use GET /entities/:key endpoint (URL encode the key)
        with self.client.get(
            f"/entities/{quote(entity_key, safe='')}",
            catch_response=True,
            name="point_by_key"
        ) as response:
            if response.status_code == 200:
                try:
                    result = response.json()
                    key = result.get("key", "unknown")
                    debug_log(f"[DEBUG] point_by_key: SUCCESS - found entity key={key[:20]}...")
                except Exception:
                    debug_log(f"[DEBUG] point_by_key: SUCCESS - status=200 (could not parse response)")
                response.success()
            elif response.status_code == 404:
                debug_log(f"[DEBUG] point_by_key: NOT_FOUND - entity_key={entity_key[:20]}...")
                response.success()  # Not found is valid
            else:
                debug_log(f"[DEBUG] point_by_key: FAILED - status={response.status_code}, entity_key={entity_key[:20]}...")
                response.failure(f"Unexpected status: {response.status_code}")
    
    @task(10)  # 10% weight
    def point_miss(self):
        """Lookup non-existent entity (guaranteed miss)."""
        # Generate a random UUID that doesn't exist
        import uuid
        random_key = f"0x{uuid.uuid4().hex}"
        debug_log(f"[DEBUG] point_miss: querying non-existent entity_key={random_key[:20]}...")
        
        # Use GET /entities/:key endpoint
        with self.client.get(
            f"/entities/{random_key}",
            catch_response=True,
            name="point_miss"
        ) as response:
            if response.status_code == 404:
                debug_log(f"[DEBUG] point_miss: SUCCESS - got expected 404 for key={random_key[:20]}...")
                response.success()  # Expected 404 for miss
            else:
                debug_log(f"[DEBUG] point_miss: FAILED - expected 404, got {response.status_code} for key={random_key[:20]}...")
                response.failure(f"Expected 404, got {response.status_code}")
    
    @task(25)  # 25% weight
    def node_filter(self):
        """Find available nodes matching filter criteria using range queries."""
        rng = random.Random()
        
        region = rng.choice(REGIONS)
        vm_type = rng.choice(VM_TYPES)
        
        # Use range queries for numeric attributes (>= operator)
        # This allows finding nodes with at least the specified resources
        min_cpu = rng.choice([4, 8, 16, 32])
        min_ram = rng.choice([16, 32, 64, 128])
        
        # Build query with filters
        # The API now supports range queries for numeric attributes:
        # - number: exact match (e.g., 8 -> "cpu_count = 8")
        # - string with operator: range query (e.g., ">=8" -> "cpu_count >= 8")
        query: Dict[str, Any] = {
            "stringAnnotations": {
                "status": "available",
                "type": "node",
                "region": region,
                "vm_type": vm_type,
            },
            "numericAnnotations": {
                # Use range queries: find nodes with at least these resources
                "cpu_count": f">={min_cpu}",  # cpu_count >= min_cpu
                "ram_gb": f">={min_ram}",     # ram_gb >= min_ram
            },
            "limit": DEFAULT_NODE_LIMIT,
        }
        
        debug_log(f"[DEBUG] node_filter: querying status=available, type=node, region={region}, "
              f"vm_type={vm_type}, cpu_count>={min_cpu}, ram_gb>={min_ram}")
        
        with self.client.post(
            "/entities/query",
            json=query,
            catch_response=True,
            name="node_filter"
        ) as response:
            if response.status_code == 200:
                try:
                    result = response.json()
                    count = result.get("count", 0)
                    debug_log(f"[DEBUG] node_filter: SUCCESS - found {count} nodes")
                except Exception:
                    debug_log(f"[DEBUG] node_filter: SUCCESS - status=200 (could not parse response)")
                response.success()
            else:
                debug_log(f"[DEBUG] node_filter: FAILED - status={response.status_code}")
                response.failure(f"Unexpected status: {response.status_code}")
    
    @task(15)  # 15% weight
    def workload_simple(self):
        """Find pending workloads (status filter only)."""
        query: Dict[str, Any] = {
            "stringAnnotations": {
                "status": "pending",
                "type": "workload",
            },
            "limit": DEFAULT_WORKLOAD_LIMIT,
        }
        
        debug_log(f"[DEBUG] workload_simple: querying status=pending, type=workload")
        
        with self.client.post(
            "/entities/query",
            json=query,
            catch_response=True,
            name="workload_simple"
        ) as response:
            if response.status_code == 200:
                try:
                    result = response.json()
                    count = result.get("count", 0)
                    debug_log(f"[DEBUG] workload_simple: SUCCESS - found {count} workloads")
                except Exception:
                    debug_log(f"[DEBUG] workload_simple: SUCCESS - status=200 (could not parse response)")
                response.success()
            else:
                debug_log(f"[DEBUG] workload_simple: FAILED - status={response.status_code}")
                response.failure(f"Unexpected status: {response.status_code}")
    
    @task(15)  # 15% weight
    def workload_specific(self):
        """Find pending workloads matching region and vm_type."""
        rng = random.Random()
        
        region = rng.choice(REGIONS)
        vm_type = rng.choice(VM_TYPES)
        
        query: Dict[str, Any] = {
            "stringAnnotations": {
                "status": "pending",
                "type": "workload",
                "region": region,
                "vm_type": vm_type,
            },
            "limit": DEFAULT_WORKLOAD_LIMIT,
        }
        
        debug_log(f"[DEBUG] workload_specific: querying status=pending, type=workload, "
              f"region={region}, vm_type={vm_type}")
        
        with self.client.post(
            "/entities/query",
            json=query,
            catch_response=True,
            name="workload_specific"
        ) as response:
            if response.status_code == 200:
                try:
                    result = response.json()
                    count = result.get("count", 0)
                    debug_log(f"[DEBUG] workload_specific: SUCCESS - found {count} workloads")
                except Exception:
                    debug_log(f"[DEBUG] workload_specific: SUCCESS - status=200 (could not parse response)")
                response.success()
            else:
                debug_log(f"[DEBUG] workload_specific: FAILED - status={response.status_code}")
                response.failure(f"Unexpected status: {response.status_code}")


# =============================================================================
# Test Initialization Hook
# =============================================================================

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Load sample data once when test starts."""
    print("=" * 60)
    print("Initializing read-only stress test")
    print("=" * 60)
    print(f"Database path: {DB_PATH}")
    print(f"Query mix: {QUERY_MIX}")
    print()
    
    GlobalSampleData.load_from_database(DB_PATH)
    
    if not GlobalSampleData.initialized:
        print("Warning: Failed to initialize sample data!")
    else:
        print("Sample data loaded successfully")
        print()

