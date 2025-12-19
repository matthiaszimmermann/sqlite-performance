"""
Locust stress test for op-geth-simulator write entities endpoint.

This test generates nodes and workloads using the same logic as append_dc_data.py
and sends them to the op-geth-simulator's POST /entities endpoint.

Usage:
    locust -f locust/write_only.py --host=http://localhost:3000
"""

import base64
import os
import random
import sys
from typing import Dict, Any

from locust import constant, task
from locust.contrib.fasthttp import FastHttpUser

# Add parent directory to path to import from src.db.append_dc_data
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db.append_dc_data import (
    NODE,
    WORKLOAD,
    NodeEntity,
    WorkloadEntity,
    create_node,
    create_workload,
)


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_CREATOR_ADDRESS = "0x0000000000000000000000000000000000dc0001"
DEFAULT_PAYLOAD_SIZE = 10000
DEFAULT_DC_NUM = 1
DEFAULT_WORKLOADS_PER_NODE = 5
DEFAULT_BLOCK = 1  # Starting block number (will be incremented per user)


# =============================================================================
# Entity Transformation
# =============================================================================

def node_to_entity_request(node: NodeEntity, creator_address: str) -> Dict[str, Any]:
    """
    Transform a NodeEntity to EntityWriteRequest format for POST /entities endpoint.
    
    Similar to node_to_sql_inserts but returns a dict for JSON API.
    Note: expiresIn is the number of blocks from current block, using random value 100-1000.
    """
    entity_key = node.entity_key
    block = node.block
    ttl = random.randint(100, 1000)  # Random TTL between 100 and 1000 blocks
    expires_at_block = block + ttl
    
    # String attributes (same as in node_to_sql_inserts)
    string_annotations = {
        "dc_id": node.dc_id,
        "type": NODE,
        "node_id": node.node_id,
        "region": node.region,
        "status": node.status,
        "vm_type": node.vm_type,
        # System attributes
        "$creator": creator_address,
        "$key": "0x" + entity_key.hex(),
        "$owner": creator_address,
    }
    
    # Numeric attributes (same as in node_to_sql_inserts)
    numeric_annotations = {
        "cpu_count": node.cpu_count,
        "ram_gb": node.ram_gb,
        "price_hour": node.price_hour,
        "avail_hours": node.avail_hours,
        # System attributes
        "$createdAtBlock": block,
        "$expiration": expires_at_block,
        "$opIndex": node.op_index,
        "$sequence": node.sequence,
        "$txIndex": node.tx_index,
    }
    
    # Encode payload as base64
    payload_base64 = base64.b64encode(node.payload).decode("utf-8")
    
    return {
        "key": "0x" + entity_key.hex(),
        "expiresIn": ttl,  # Number of blocks from current block (random 100-1000)
        "payload": payload_base64,
        "contentType": "application/octet-stream",
        "ownerAddress": creator_address,
        "stringAnnotations": string_annotations,
        "numericAnnotations": numeric_annotations,
    }


def workload_to_entity_request(workload: WorkloadEntity, creator_address: str) -> Dict[str, Any]:
    """
    Transform a WorkloadEntity to EntityWriteRequest format for POST /entities endpoint.
    
    Similar to workload_to_sql_inserts but returns a dict for JSON API.
    Note: expiresIn is the number of blocks from current block, using random value 100-1000.
    """
    entity_key = workload.entity_key
    block = workload.block
    ttl = random.randint(100, 1000)  # Random TTL between 100 and 1000 blocks
    expires_at_block = block + ttl
    
    # String attributes (same as in workload_to_sql_inserts)
    string_annotations = {
        "dc_id": workload.dc_id,
        "type": WORKLOAD,
        "workload_id": workload.workload_id,
        "status": workload.status,
        "assigned_node": workload.assigned_node,
        "region": workload.region,
        "vm_type": workload.vm_type,
        # System attributes
        "$creator": creator_address,
        "$key": "0x" + entity_key.hex(),
        "$owner": creator_address,
    }
    
    # Numeric attributes (same as in workload_to_sql_inserts)
    numeric_annotations = {
        "req_cpu": workload.req_cpu,
        "req_ram": workload.req_ram,
        "max_hours": workload.max_hours,
        # System attributes
        "$createdAtBlock": block,
        "$expiration": expires_at_block,
        "$opIndex": workload.op_index,
        "$sequence": workload.sequence,
        "$txIndex": workload.tx_index,
    }
    
    # Encode payload as base64
    payload_base64 = base64.b64encode(workload.payload).decode("utf-8")
    
    return {
        "key": "0x" + entity_key.hex(),
        "expiresIn": ttl,  # Number of blocks from current block (random 100-1000)
        "payload": payload_base64,
        "contentType": "application/octet-stream",
        "ownerAddress": creator_address,
        "stringAnnotations": string_annotations,
        "numericAnnotations": numeric_annotations,
    }


# =============================================================================
# Locust User Class
# =============================================================================

class DataCenterUser(FastHttpUser):
    """
    Locust user that generates nodes and workloads and sends them to op-geth-simulator.
    
    Each user maintains its own counters for unique entity IDs.
    """
    wait_time = constant(1)
    
    # Per-user state
    node_counter: int = 0
    workload_counter: int = 0
    current_block: int = DEFAULT_BLOCK
    seed: int = None
    creator_address: str = DEFAULT_CREATOR_ADDRESS
    payload_size: int = DEFAULT_PAYLOAD_SIZE
    dc_num: int = DEFAULT_DC_NUM
    workloads_per_node: int = DEFAULT_WORKLOADS_PER_NODE
    
    def on_start(self):
        """Initialize user-specific state when user starts."""
        # Generate unique seed for this user (based on user ID)
        user_id = getattr(self, "user_id", random.randint(1, 2**31 - 1))
        self.seed = user_id
        self.node_counter = 0
        self.workload_counter = 0
        self.current_block = DEFAULT_BLOCK
        
        # Randomize some parameters per user for variety
        self.payload_size = random.randint(5000, 15000)
        self.workloads_per_node = random.randint(3, 7)
    
    @task
    def write_node_with_workloads(self):
        """
        Generate one node and 5 workloads for that node, then send them to the API.
        
        This is the main task that will be executed repeatedly.
        """
        # Increment counters
        self.node_counter += 1
        self.current_block += 1
        
        # Create the node
        node = create_node(
            dc_num=self.dc_num,
            node_num=self.node_counter,
            payload_size=self.payload_size,
            block=self.current_block,
            seed=self.seed,
        )
        
        # Transform node to API format
        node_request = node_to_entity_request(node, self.creator_address)
        
        # Send node to API
        with self.client.post(
            "/entities",
            json=node_request,
            catch_response=True,
            name="write_node"
        ) as response:
            if response.status_code == 202:
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")
        
        # Create workloads for this node
        # First workload is assigned if node is busy
        is_busy = node.status == "busy"
        
        for wl_idx in range(self.workloads_per_node):
            self.workload_counter += 1
            
            # First workload is assigned if node is busy
            if is_busy and wl_idx == 0:
                wl_status = "running"
                wl_assigned = node.node_id
            else:
                wl_status = "pending"
                wl_assigned = ""
            
            # Create workload
            workload = create_workload(
                dc_num=self.dc_num,
                workload_num=self.workload_counter,
                nodes_per_dc=self.node_counter,  # Not used when assigned_node provided
                payload_size=self.payload_size,
                block=self.current_block,
                seed=self.seed,
                status=wl_status,
                assigned_node=wl_assigned,
            )
            
            # Transform workload to API format
            workload_request = workload_to_entity_request(workload, self.creator_address)
            
            # Send workload to API
            with self.client.post(
                "/entities",
                json=workload_request,
                catch_response=True,
                name="write_workload"
            ) as response:
                if response.status_code == 202:
                    response.success()
                else:
                    response.failure(f"Unexpected status: {response.status_code}")

