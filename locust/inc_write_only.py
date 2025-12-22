"""
Locust stress test for op-geth-simulator write entities endpoint with incremental attributes.

This test gradually increases the number of numeric and string attributes on entities
over time to measure performance impact of attribute count.

Usage:
    locust -f locust/inc_write_only.py --host=http://localhost:3000
"""

import base64
import os
import random
import uuid
from typing import Dict, Any, Optional

from locust import constant, task
from locust.contrib.fasthttp import FastHttpUser


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_CREATOR_ADDRESS = "0x0000000000000000000000000000000000dc0001"
DEFAULT_PAYLOAD_SIZE = 10000
DEFAULT_PERIOD_RUNS = 300  # Number of runs per attribute configuration


# =============================================================================
# Locust User Class
# =============================================================================

class IncrementalWriteUser(FastHttpUser):
    """
    Locust user that writes entities with incrementally increasing attribute counts.
    
    The progression is:
    - First period: no attributes
    - Next 6 periods: 1-6 numeric attributes (no string)
    - Next 6 periods: 1-6 string attributes (no numeric)
    - After that: 6 numeric + 6 string attributes constantly
    """
    wait_time = constant(1)  # One task per second
    
    # Per-user state
    task_count: int = 0
    payload_size: int = DEFAULT_PAYLOAD_SIZE
    period_runs: int = DEFAULT_PERIOD_RUNS
    creator_address: str = DEFAULT_CREATOR_ADDRESS
    
    def on_start(self):
        """Initialize user-specific state when user starts."""
        self.task_count = 0
        self.payload_size = DEFAULT_PAYLOAD_SIZE
        self.period_runs = DEFAULT_PERIOD_RUNS
    
    def get_attribute_config(self) -> tuple[int, int]:
        """
        Determine the number of numeric and string attributes based on task count.
        
        Returns:
            Tuple of (numeric_attr_count, string_attr_count)
        """
        period = self.task_count // self.period_runs
        
        # First period (0): no attributes
        if period == 0:
            return (0, 0)
        
        # Periods 1-6: 1-6 numeric attributes
        elif 1 <= period <= 6:
            numeric_count = period
            return (numeric_count, 0)
        
        # Periods 7-12: 1-6 string attributes (no numeric)
        elif 7 <= period <= 12:
            string_count = period - 6
            return (0, string_count)
        
        # Period 13+: 6 numeric + 6 string attributes
        else:
            return (6, 6)
    
    def generate_attributes(
        self, 
        numeric_count: int, 
        string_count: int
    ) -> tuple[Optional[Dict[str, float]], Optional[Dict[str, str]]]:
        """
        Generate numeric and string attributes.
        
        Args:
            numeric_count: Number of numeric attributes to generate
            string_count: Number of string attributes to generate
            
        Returns:
            Tuple of (numeric_annotations, string_annotations)
        """
        numeric_annotations = None
        string_annotations = None
        
        if numeric_count > 0:
            numeric_annotations = {}
            for i in range(numeric_count):
                attr_name = f"numeric_attr_{i+1}"
                # Generate random numeric value
                numeric_annotations[attr_name] = random.uniform(0, 1000)
        
        if string_count > 0:
            string_annotations = {}
            for i in range(string_count):
                attr_name = f"string_attr_{i+1}"
                # Generate random string value
                string_annotations[attr_name] = f"value_{random.randint(1, 10000)}"
        
        return (numeric_annotations, string_annotations)
    
    def generate_payload(self, size: int) -> str:
        """Generate a random payload of given size and return as base64."""
        payload_bytes = os.urandom(size)
        return base64.b64encode(payload_bytes).decode("utf-8")
    
    def create_entity_request(
        self,
        numeric_annotations: Optional[Dict[str, float]],
        string_annotations: Optional[Dict[str, str]]
    ) -> Dict[str, Any]:
        """Create an entity write request."""
        # Generate unique entity key
        entity_key = "0x" + uuid.uuid4().hex
        
        # Random TTL between 100 and 1000 blocks
        expires_in = random.randint(100, 1000)
        
        # Generate payload
        payload_base64 = self.generate_payload(self.payload_size)
        
        request = {
            "key": entity_key,
            "expiresIn": expires_in,
            "payload": payload_base64,
            "contentType": "application/octet-stream",
            "ownerAddress": self.creator_address,
        }
        
        # Add annotations if they exist
        if string_annotations:
            request["stringAnnotations"] = string_annotations
        
        if numeric_annotations:
            request["numericAnnotations"] = numeric_annotations
        
        return request
    
    @task
    def write_entity(self):
        """
        Write a single entity with attributes determined by current task count.
        
        This task is executed once per second.
        """
        # Increment task counter
        self.task_count += 1
        
        # Get attribute configuration based on task count
        numeric_count, string_count = self.get_attribute_config()
        
        # Generate attributes
        numeric_annotations, string_annotations = self.generate_attributes(
            numeric_count, 
            string_count
        )
        
        # Create entity request
        entity_request = self.create_entity_request(
            numeric_annotations,
            string_annotations
        )
        
        # Send to API
        with self.client.post(
            "/entities",
            json=entity_request,
            catch_response=True,
            name=f"write_entity_n{numeric_count}_s{string_count}"
        ) as response:
            if response.status_code == 202:
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

