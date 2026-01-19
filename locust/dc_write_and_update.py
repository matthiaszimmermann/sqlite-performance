"""
Locust stress test for op-geth-simulator with write + update-like operations.

Important note about "updates":
The simulator only exposes POST /entities (no PUT/PATCH). To simulate updates we
re-POST the *same entity key* with modified annotations (e.g. status changes).

This test has 4 task types with relative frequency (lowest -> highest):
  - add_node (least often)
  - update_node
  - add_workload (assigned to an existing node)
  - update_workload (most often; status + assignment)

Each Locust user keeps an in-memory pool (ring buffer) of entities:
  - up to 1000 nodes
  - up to 5000 workloads

Usage:
    locust -f locust/dc_write_and_update.py --host=http://localhost:3000
"""

import base64
import os
import random
import sys
from dataclasses import replace
from typing import Any, Dict, List, Optional

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
# Configuration (env-overridable)
# =============================================================================

DEFAULT_CREATOR_ADDRESS = os.getenv(
    "DC_CREATOR_ADDRESS", "0x0000000000000000000000000000000000dc0001"
)
DEFAULT_BLOCK = int(os.getenv("DC_START_BLOCK", "1"))
DEFAULT_DC_NUM = int(os.getenv("DC_NUM", "1"))

NODE_POOL_SIZE = int(os.getenv("DC_NODE_POOL_SIZE", "1000"))
WORKLOAD_POOL_SIZE = int(os.getenv("DC_WORKLOAD_POOL_SIZE", "5000"))

# Payload size is randomized per user, but bounded by these env vars
PAYLOAD_SIZE_MIN = int(os.getenv("DC_PAYLOAD_SIZE_MIN", "5000"))
PAYLOAD_SIZE_MAX = int(os.getenv("DC_PAYLOAD_SIZE_MAX", "15000"))

# Task weights (relative frequencies)
W_ADD_NODE = int(os.getenv("DC_W_ADD_NODE", "1"))
W_UPDATE_NODE = int(os.getenv("DC_W_UPDATE_NODE", "3"))
W_ADD_WORKLOAD = int(os.getenv("DC_W_ADD_WORKLOAD", "10"))
W_UPDATE_WORKLOAD = int(os.getenv("DC_W_UPDATE_WORKLOAD", "20"))


# =============================================================================
# Entity Transformation (API shape)
# =============================================================================

def _encode_payload(payload: bytes) -> str:
    return base64.b64encode(payload).decode("utf-8")


def node_to_entity_request(node: NodeEntity, creator_address: str) -> Dict[str, Any]:
    """
    Transform a NodeEntity to EntityWriteRequest format for POST /entities endpoint.

    expiresIn is the number of blocks from current block. We keep it small-ish to
    keep churn high during stress tests.
    """
    entity_key = node.entity_key
    block = node.block
    ttl = random.randint(100, 1000)
    expires_at_block = block + ttl

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

    return {
        "key": "0x" + entity_key.hex(),
        "expiresIn": ttl,
        "payload": _encode_payload(node.payload),
        "contentType": "application/octet-stream",
        "ownerAddress": creator_address,
        "stringAnnotations": string_annotations,
        "numericAnnotations": numeric_annotations,
    }


def workload_to_entity_request(workload: WorkloadEntity, creator_address: str) -> Dict[str, Any]:
    """
    Transform a WorkloadEntity to EntityWriteRequest format for POST /entities endpoint.

    expiresIn is the number of blocks from current block. We keep it small-ish to
    keep churn high during stress tests.
    """
    entity_key = workload.entity_key
    block = workload.block
    ttl = random.randint(100, 1000)
    expires_at_block = block + ttl

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

    return {
        "key": "0x" + entity_key.hex(),
        "expiresIn": ttl,
        "payload": _encode_payload(workload.payload),
        "contentType": "application/octet-stream",
        "ownerAddress": creator_address,
        "stringAnnotations": string_annotations,
        "numericAnnotations": numeric_annotations,
    }


# =============================================================================
# Locust User
# =============================================================================

class DataCenterWriteAndUpdateUser(FastHttpUser):
    """
    Locust user that does create + update-like operations via POST /entities.

    Pools are per-user to keep behavior deterministic and avoid coordination between users.
    """

    wait_time = constant(1)

    # Per-user state
    seed: int
    dc_num: int
    creator_address: str
    current_block: int
    payload_size: int

    node_counter: int
    workload_counter: int

    nodes: List[NodeEntity]
    workloads: List[WorkloadEntity]
    node_ring_idx: int
    workload_ring_idx: int

    rng: random.Random

    def on_start(self) -> None:
        user_id = getattr(self, "user_id", random.randint(1, 2**31 - 1))
        self.seed = int(user_id)
        self.rng = random.Random(self.seed)

        self.dc_num = DEFAULT_DC_NUM
        self.creator_address = DEFAULT_CREATOR_ADDRESS
        self.current_block = DEFAULT_BLOCK

        # Keep payloads different across users but stable within a user
        self.payload_size = self.rng.randint(PAYLOAD_SIZE_MIN, PAYLOAD_SIZE_MAX)

        self.node_counter = 0
        self.workload_counter = 0

        self.nodes = []
        self.workloads = []
        self.node_ring_idx = 0
        self.workload_ring_idx = 0

    # -------------------------------------------------------------------------
    # Pool helpers
    # -------------------------------------------------------------------------

    def _pool_put_node(self, node: NodeEntity) -> None:
        if len(self.nodes) < NODE_POOL_SIZE:
            self.nodes.append(node)
            return
        self.nodes[self.node_ring_idx] = node
        self.node_ring_idx = (self.node_ring_idx + 1) % NODE_POOL_SIZE

    def _pool_put_workload(self, workload: WorkloadEntity) -> None:
        if len(self.workloads) < WORKLOAD_POOL_SIZE:
            self.workloads.append(workload)
            return
        self.workloads[self.workload_ring_idx] = workload
        self.workload_ring_idx = (self.workload_ring_idx + 1) % WORKLOAD_POOL_SIZE

    def _pick_node(self) -> Optional[NodeEntity]:
        if not self.nodes:
            return None
        return self.rng.choice(self.nodes)

    def _pick_workload(self) -> Optional[WorkloadEntity]:
        if not self.workloads:
            return None
        return self.rng.choice(self.workloads)

    # -------------------------------------------------------------------------
    # Domain helpers (status/assignment changes)
    # -------------------------------------------------------------------------

    def _sample_node_status_for_update(self, prev: str) -> str:
        # Bias towards "available" / "busy" changes; keep "offline" rare.
        r = self.rng.random()
        if r < 0.05:
            return "offline"
        if r < 0.55:
            return "available"
        return "busy"

    def _sample_workload_status_for_update(self, prev: str) -> str:
        # Keep workloads churny: mostly running <-> pending, with occasional completed.
        r = self.rng.random()
        if r < 0.05:
            return "completed"
        if r < 0.55:
            return "running"
        return "pending"

    def _workload_assignment_for_status(self, status: str) -> str:
        if status == "running":
            node = self._pick_node()
            return node.node_id if node else ""
        # pending/completed => unassigned
        return ""

    # -------------------------------------------------------------------------
    # Core operations (POST /entities)
    # -------------------------------------------------------------------------

    def _post_entity(self, req: Dict[str, Any], name: str) -> None:
        with self.client.post("/entities", json=req, catch_response=True, name=name) as resp:
            if resp.status_code == 202:
                resp.success()
            else:
                resp.failure(f"Unexpected status: {resp.status_code}")

    def _put_entity_update(self, key: str, patch: Dict[str, Any], name: str) -> None:
        # Server-side UpdateRequest expects full entity data. We allow callers to pass
        # a full create-like request and strip the key (key comes from URL path).
        body = dict(patch)
        body.pop("key", None)
        with self.client.put(f"/entities/{key}", json=body, catch_response=True, name=name) as resp:
            if resp.status_code == 202:
                resp.success()
            else:
                resp.failure(f"Unexpected status: {resp.status_code}")

    # -------------------------------------------------------------------------
    # Tasks (frequency: add_node < update_node < add_workload < update_workload)
    # -------------------------------------------------------------------------

    @task(W_ADD_NODE)
    def add_node(self) -> None:
        self.node_counter += 1
        self.current_block += 1

        # Prefer available nodes; updates will flip to busy/offline.
        node = create_node(
            dc_num=self.dc_num,
            node_num=self.node_counter,
            payload_size=self.payload_size,
            block=self.current_block,
            seed=self.seed,
            status="available",
        )

        self._post_entity(node_to_entity_request(node, self.creator_address), name="add_node")
        self._pool_put_node(node)

    @task(W_UPDATE_NODE)
    def update_node(self) -> None:
        node = self._pick_node()
        if node is None:
            # bootstrap
            self.add_node()
            return

        self.current_block += 1
        new_status = self._sample_node_status_for_update(node.status)
        updated = replace(node, status=new_status, block=self.current_block)

        key_hex = "0x" + updated.entity_key.hex()
        # Update existing entity by key (send full UpdateRequest payload).
        req = node_to_entity_request(updated, self.creator_address)
        self._put_entity_update(
            key_hex,
            patch=req,
            name="update_node",
        )

        # Persist the latest version in the pool (by replacement in-place)
        try:
            idx = self.nodes.index(node)
            self.nodes[idx] = updated
        except ValueError:
            self._pool_put_node(updated)

    @task(W_ADD_WORKLOAD)
    def add_workload(self) -> None:
        if not self.nodes:
            self.add_node()

        self.workload_counter += 1
        self.current_block += 1

        # Per requirement: new workloads are assigned to some node.
        assigned_node_id = self._pick_node().node_id if self.nodes else ""

        workload = create_workload(
            dc_num=self.dc_num,
            workload_num=self.workload_counter,
            nodes_per_dc=max(1, self.node_counter),
            payload_size=self.payload_size,
            block=self.current_block,
            seed=self.seed,
            status="running",
            assigned_node=assigned_node_id,
        )

        self._post_entity(
            workload_to_entity_request(workload, self.creator_address), name="add_workload"
        )
        self._pool_put_workload(workload)

    @task(W_UPDATE_WORKLOAD)
    def update_workload(self) -> None:
        workload = self._pick_workload()
        if workload is None:
            # bootstrap: ensure we have at least one workload
            self.add_workload()
            return

        if not self.nodes:
            self.add_node()

        self.current_block += 1
        new_status = self._sample_workload_status_for_update(workload.status)
        new_assigned = self._workload_assignment_for_status(new_status)

        updated = replace(
            workload,
            status=new_status,
            assigned_node=new_assigned,
            block=self.current_block,
        )

        key_hex = "0x" + updated.entity_key.hex()
        # Update existing entity by key (send full UpdateRequest payload).
        req = workload_to_entity_request(updated, self.creator_address)
        self._put_entity_update(
            key_hex,
            patch=req,
            name="update_workload",
        )

        # Persist the latest version in the pool (by replacement in-place)
        try:
            idx = self.workloads.index(workload)
            self.workloads[idx] = updated
        except ValueError:
            self._pool_put_workload(updated)


