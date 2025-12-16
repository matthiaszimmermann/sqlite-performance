"""
Inspect a Data Center database and report statistics.

Usage:
    uv run python -m src.db.inspect_dc_db data/dc_test.db
    uv run python -m src.db.inspect_dc_db data/dc_seed_2x.db
"""

import argparse
import os
import random
import sqlite3
import sys


# =============================================================================
# Configuration
# =============================================================================

# Memory allocation for SQLite in GB (adjust based on available RAM)
MEMORY_GB = 16


def configure_connection(conn: sqlite3.Connection) -> None:
    """Configure SQLite connection for optimal read performance."""
    # For read-only workloads: small cache, large mmap
    # mmap lets the OS kernel manage caching efficiently for scans
    cache_mb = 256  # Small cache for index lookups
    mmap_gb = MEMORY_GB - 1  # Most memory to mmap
    
    # cache_size in KB (negative = KB)
    cache_kb = cache_mb * 1024
    conn.execute(f"PRAGMA cache_size = -{cache_kb}")
    
    # mmap_size in bytes
    mmap_bytes = mmap_gb * 1024 * 1024 * 1024
    conn.execute(f"PRAGMA mmap_size = {mmap_bytes}")
    
    # Read-only optimizations
    conn.execute("PRAGMA temp_store = MEMORY")


def format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string."""
    if size_bytes >= 1024**3:
        return f"{size_bytes / 1024**3:.2f} GB"
    elif size_bytes >= 1024**2:
        return f"{size_bytes / 1024**2:.2f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes} bytes"


def format_ttl_blocks(ttl_blocks: int) -> str:
    """Format TTL in blocks as human-readable string (assuming 2s block time)."""
    if ttl_blocks is None:
        return "N/A"
    seconds = ttl_blocks * 2
    if seconds >= 86400:
        days = seconds / 86400
        return f"{ttl_blocks:,} blocks (~{days:.1f} days)"
    elif seconds >= 3600:
        hours = seconds / 3600
        return f"{ttl_blocks:,} blocks (~{hours:.1f} hours)"
    else:
        minutes = seconds / 60
        return f"{ttl_blocks:,} blocks (~{minutes:.1f} min)"


def get_random_entity(conn: sqlite3.Connection, entity_type: str) -> dict | None:
    """
    Fetch a random entity of the given type with all its attributes.
    
    Returns dict with:
        - entity_key: hex string
        - from_block: int
        - to_block: int
        - string_attrs: dict of key->value
        - numeric_attrs: dict of key->value
        - payload_size: int
    """
    cursor = conn.cursor()
    
    # Get a random entity key of the given type
    cursor.execute("""
        SELECT entity_key FROM string_attributes 
        WHERE key = 'type' AND value = ?
        ORDER BY RANDOM() LIMIT 1
    """, (entity_type,))
    
    row = cursor.fetchone()
    if not row:
        return None
    
    entity_key = row[0]
    
    result = {
        "entity_key": entity_key.hex() if isinstance(entity_key, bytes) else entity_key,
        "from_block": None,
        "to_block": None,
        "string_attrs": {},
        "numeric_attrs": {},
        "payload_size": 0,
    }
    
    # Get from_block and to_block from payloads table
    cursor.execute("""
        SELECT from_block, to_block, LENGTH(payload) FROM payloads 
        WHERE entity_key = ?
        LIMIT 1
    """, (entity_key,))
    row = cursor.fetchone()
    if row:
        result["from_block"] = row[0]
        result["to_block"] = row[1]
        result["payload_size"] = row[2] if row[2] else 0
    
    # Get string attributes (excluding system attrs for cleaner output)
    cursor.execute("""
        SELECT key, value FROM string_attributes 
        WHERE entity_key = ?
        AND key NOT LIKE '$%'
        AND key != 'type'
        ORDER BY key
    """, (entity_key,))
    for key, value in cursor.fetchall():
        result["string_attrs"][key] = value
    
    # Get numeric attributes (excluding system attrs)
    cursor.execute("""
        SELECT key, value FROM numeric_attributes 
        WHERE entity_key = ?
        AND key NOT LIKE '$%'
        ORDER BY key
    """, (entity_key,))
    for key, value in cursor.fetchall():
        result["numeric_attrs"][key] = value
    
    return result


def inspect_database(db_path: str) -> dict:
    """
    Inspect a database and return statistics.
    
    Returns dict with:
        - file_size: Size in bytes
        - datacenters: List of DC IDs
        - num_nodes: Total node count
        - num_workloads: Total workload count
        - workloads_per_node: Ratio
        - total_entities: Total entity count
        - avg_str_attrs_per_entity: Average string attributes
        - avg_num_attrs_per_entity: Average numeric attributes
        - avg_payload_size: Average payload size in bytes
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")
    
    stats = {}
    
    # File size
    stats["file_size"] = os.path.getsize(db_path)
    
    conn = sqlite3.connect(db_path)
    configure_connection(conn)
    cursor = conn.cursor()
    
    # Get data centers (distinct dc_id values)
    cursor.execute("""
        SELECT DISTINCT value FROM string_attributes 
        WHERE key = 'dc_id' 
        ORDER BY value
    """)
    stats["datacenters"] = [row[0] for row in cursor.fetchall()]
    
    # Count nodes (entities with type='node')
    cursor.execute("""
        SELECT COUNT(DISTINCT entity_key) FROM string_attributes 
        WHERE key = 'type' AND value = 'node'
    """)
    stats["num_nodes"] = cursor.fetchone()[0]
    
    # Count workloads (entities with type='workload')
    cursor.execute("""
        SELECT COUNT(DISTINCT entity_key) FROM string_attributes 
        WHERE key = 'type' AND value = 'workload'
    """)
    stats["num_workloads"] = cursor.fetchone()[0]
    
    # Calculate workloads per node ratio
    if stats["num_nodes"] > 0:
        stats["workloads_per_node"] = stats["num_workloads"] / stats["num_nodes"]
    else:
        stats["workloads_per_node"] = 0.0
    
    # Total entities (from payloads table - one row per entity)
    cursor.execute("SELECT COUNT(*) FROM payloads")
    stats["total_entities"] = cursor.fetchone()[0]
    
    # If no payloads, try counting from string_attributes
    if stats["total_entities"] == 0:
        cursor.execute("SELECT COUNT(DISTINCT entity_key) FROM string_attributes")
        stats["total_entities"] = cursor.fetchone()[0]
    
    # Average string attributes per entity
    cursor.execute("""
        SELECT COUNT(*) FROM string_attributes
    """)
    total_str_attrs = cursor.fetchone()[0]
    if stats["total_entities"] > 0:
        stats["avg_str_attrs_per_entity"] = total_str_attrs / stats["total_entities"]
    else:
        stats["avg_str_attrs_per_entity"] = 0.0
    
    # Average numeric attributes per entity
    cursor.execute("""
        SELECT COUNT(*) FROM numeric_attributes
    """)
    total_num_attrs = cursor.fetchone()[0]
    if stats["total_entities"] > 0:
        stats["avg_num_attrs_per_entity"] = total_num_attrs / stats["total_entities"]
    else:
        stats["avg_num_attrs_per_entity"] = 0.0
    
    # Average payload size
    cursor.execute("""
        SELECT AVG(LENGTH(payload)) FROM payloads
    """)
    result = cursor.fetchone()[0]
    stats["avg_payload_size"] = result if result is not None else 0.0
    
    # Additional stats
    cursor.execute("SELECT COUNT(*) FROM string_attributes")
    stats["total_str_attrs"] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM numeric_attributes")
    stats["total_num_attrs"] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM payloads")
    stats["total_payloads"] = cursor.fetchone()[0]
    
    # Total rows
    stats["total_rows"] = stats["total_str_attrs"] + stats["total_num_attrs"] + stats["total_payloads"]
    
    # TTL statistics for nodes (to_block - from_block)
    cursor.execute("""
        SELECT MIN(p.to_block - p.from_block), MAX(p.to_block - p.from_block)
        FROM payloads p
        JOIN string_attributes sa ON p.entity_key = sa.entity_key
        WHERE sa.key = 'type' AND sa.value = 'node'
        AND p.to_block < 9223372036854775807
    """)
    row = cursor.fetchone()
    stats["node_ttl_min"] = row[0] if row and row[0] is not None else None
    stats["node_ttl_max"] = row[1] if row and row[1] is not None else None
    
    # TTL statistics for workloads
    cursor.execute("""
        SELECT MIN(p.to_block - p.from_block), MAX(p.to_block - p.from_block)
        FROM payloads p
        JOIN string_attributes sa ON p.entity_key = sa.entity_key
        WHERE sa.key = 'type' AND sa.value = 'workload'
        AND p.to_block < 9223372036854775807
    """)
    row = cursor.fetchone()
    stats["workload_ttl_min"] = row[0] if row and row[0] is not None else None
    stats["workload_ttl_max"] = row[1] if row and row[1] is not None else None
    
    # Get random example entities
    stats["example_node"] = get_random_entity(conn, "node")
    stats["example_workload"] = get_random_entity(conn, "workload")
    
    conn.close()
    return stats


def format_entity_example(entity: dict, entity_type: str) -> str:
    """Format an entity example for display."""
    lines = []
    lines.append(f"  Entity Key: {entity['entity_key']}")
    
    # Block range info
    from_block = entity.get('from_block')
    to_block = entity.get('to_block')
    if from_block is not None and to_block is not None:
        ttl = to_block - from_block
        lines.append(f"  From Block: {from_block:,}")
        lines.append(f"  To Block:   {to_block:,} (TTL: {format_ttl_blocks(ttl)})")
    
    lines.append("  String Attributes:")
    for key, value in sorted(entity["string_attrs"].items()):
        if key == "type":
            continue  # Already known
        lines.append(f"    {key}: {value}")
    
    lines.append("  Numeric Attributes:")
    for key, value in sorted(entity["numeric_attrs"].items()):
        lines.append(f"    {key}: {value}")
    
    lines.append(f"  Payload Size: {format_size(entity['payload_size'])}")
    
    return "\n".join(lines)


def print_report(db_path: str, stats: dict):
    """Print a formatted report of database statistics."""
    print("=" * 60)
    print("Data Center Database Inspection")
    print("=" * 60)
    print(f"Database:              {db_path}")
    print(f"File size:             {format_size(stats['file_size'])}")
    print()
    
    print("--- Entity Counts ---")
    print(f"Data centers:          {len(stats['datacenters'])}")
    if stats['datacenters']:
        dc_list = ", ".join(stats['datacenters'][:10])
        if len(stats['datacenters']) > 10:
            dc_list += f", ... (+{len(stats['datacenters']) - 10} more)"
        print(f"  IDs:                 {dc_list}")
    print(f"Nodes:                 {stats['num_nodes']:,}")
    print(f"Workloads:             {stats['num_workloads']:,}")
    print(f"Workloads/node ratio:  {stats['workloads_per_node']:.2f}")
    print(f"Total entities:        {stats['total_entities']:,}")
    print()
    
    print("--- Attribute Statistics ---")
    print(f"Avg string attrs/entity:   {stats['avg_str_attrs_per_entity']:.1f}")
    print(f"Avg numeric attrs/entity:  {stats['avg_num_attrs_per_entity']:.1f}")
    print(f"Avg payload size:          {format_size(int(stats['avg_payload_size']))}")
    print()
    
    print("--- TTL Statistics (block time = 2s) ---")
    if stats.get("node_ttl_min") is not None:
        print(f"Node TTL min:              {format_ttl_blocks(stats['node_ttl_min'])}")
        print(f"Node TTL max:              {format_ttl_blocks(stats['node_ttl_max'])}")
    else:
        print("Node TTL:                  No nodes with finite TTL")
    if stats.get("workload_ttl_min") is not None:
        print(f"Workload TTL min:          {format_ttl_blocks(stats['workload_ttl_min'])}")
        print(f"Workload TTL max:          {format_ttl_blocks(stats['workload_ttl_max'])}")
    else:
        print("Workload TTL:              No workloads with finite TTL")
    print()
    
    print("--- Row Counts ---")
    print(f"String attributes:     {stats['total_str_attrs']:,}")
    print(f"Numeric attributes:    {stats['total_num_attrs']:,}")
    print(f"Payloads:              {stats['total_payloads']:,}")
    print(f"Total rows:            {stats['total_rows']:,}")
    print()
    
    print("--- Example Entities ---")
    if stats.get("example_node"):
        print("Example Node:")
        print(format_entity_example(stats["example_node"], "node"))
    else:
        print("No nodes found in database")
    print()
    
    if stats.get("example_workload"):
        print("Example Workload:")
        print(format_entity_example(stats["example_workload"], "workload"))
    else:
        print("No workloads found in database")
    
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Inspect a Data Center database and report statistics"
    )
    parser.add_argument(
        "database",
        type=str,
        help="Path to the database file to inspect"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of formatted text"
    )
    
    args = parser.parse_args()
    
    try:
        stats = inspect_database(args.database)
        
        if args.json:
            import json
            print(json.dumps(stats, indent=2))
        else:
            print_report(args.database, stats)
            
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
