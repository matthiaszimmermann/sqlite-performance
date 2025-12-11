#!/usr/bin/env python3
"""Demo script for EVA (Entity-Value-Attribute) pattern with SQLite."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "demo.db"


def create_database() -> sqlite3.Connection:
    """Create the database and return a connection."""
    # Remove existing database for fresh start
    if DB_PATH.exists():
        DB_PATH.unlink()
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    print(f"Created database: {DB_PATH}")
    return conn


def create_eva_schema(conn: sqlite3.Connection) -> None:
    """Create the EVA schema with entity and attribute tables."""
    conn.executescript("""
        -- Entity table: stores all entities with a type
        CREATE TABLE entity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- String attributes table
        CREATE TABLE attribute_string (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            attribute_name TEXT NOT NULL,
            attribute_value TEXT,
            FOREIGN KEY (entity_id) REFERENCES entity(id) ON DELETE CASCADE
        );
        
        -- Integer attributes table
        CREATE TABLE attribute_int (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            attribute_name TEXT NOT NULL,
            attribute_value INTEGER,
            FOREIGN KEY (entity_id) REFERENCES entity(id) ON DELETE CASCADE
        );
        
        -- Indexes for faster attribute lookups
        CREATE INDEX idx_attr_string_entity ON attribute_string(entity_id);
        CREATE INDEX idx_attr_string_name ON attribute_string(attribute_name);
        CREATE INDEX idx_attr_int_entity ON attribute_int(entity_id);
        CREATE INDEX idx_attr_int_name ON attribute_int(attribute_name);
    """)
    conn.commit()
    print("Created EVA schema (entity, attribute_string, attribute_int)")


def insert_entity(
    conn: sqlite3.Connection,
    entity_type: str,
    string_attrs: dict[str, str] | None = None,
    int_attrs: dict[str, int] | None = None,
) -> int:
    """Insert an entity with its attributes.
    
    Args:
        conn: Database connection
        entity_type: Type of entity (e.g., 'person', 'product')
        string_attrs: Dictionary of string attribute name -> value
        int_attrs: Dictionary of integer attribute name -> value
        
    Returns:
        The ID of the created entity
    """
    cursor = conn.cursor()
    
    # Insert entity
    cursor.execute("INSERT INTO entity (entity_type) VALUES (?)", (entity_type,))
    entity_id = cursor.lastrowid
    
    # Insert string attributes
    if string_attrs:
        cursor.executemany(
            "INSERT INTO attribute_string (entity_id, attribute_name, attribute_value) VALUES (?, ?, ?)",
            [(entity_id, name, value) for name, value in string_attrs.items()]
        )
    
    # Insert integer attributes
    if int_attrs:
        cursor.executemany(
            "INSERT INTO attribute_int (entity_id, attribute_name, attribute_value) VALUES (?, ?, ?)",
            [(entity_id, name, value) for name, value in int_attrs.items()]
        )
    
    conn.commit()
    return entity_id


def get_entity(conn: sqlite3.Connection, entity_id: int) -> dict | None:
    """Retrieve an entity with all its attributes.
    
    Args:
        conn: Database connection
        entity_id: ID of the entity to retrieve
        
    Returns:
        Dictionary with entity data and attributes, or None if not found
    """
    cursor = conn.cursor()
    
    # Get entity
    cursor.execute("SELECT * FROM entity WHERE id = ?", (entity_id,))
    entity_row = cursor.fetchone()
    
    if not entity_row:
        return None
    
    result = {
        "id": entity_row["id"],
        "entity_type": entity_row["entity_type"],
        "created_at": entity_row["created_at"],
        "attributes": {}
    }
    
    # Get string attributes
    cursor.execute(
        "SELECT attribute_name, attribute_value FROM attribute_string WHERE entity_id = ?",
        (entity_id,)
    )
    for row in cursor.fetchall():
        result["attributes"][row["attribute_name"]] = row["attribute_value"]
    
    # Get integer attributes
    cursor.execute(
        "SELECT attribute_name, attribute_value FROM attribute_int WHERE entity_id = ?",
        (entity_id,)
    )
    for row in cursor.fetchall():
        result["attributes"][row["attribute_name"]] = row["attribute_value"]
    
    return result


def main():
    """Run the demo."""
    print("=" * 60)
    print("SQLite EVA (Entity-Value-Attribute) Demo")
    print("=" * 60)
    print()
    
    # Create database
    conn = create_database()
    
    # Create schema
    create_eva_schema(conn)
    print()
    
    # Insert some entities
    print("Inserting entities...")
    
    alice_id = insert_entity(
        conn,
        entity_type="person",
        string_attrs={"name": "Alice", "email": "alice@example.com", "city": "New York"},
        int_attrs={"age": 30, "score": 95}
    )
    print(f"  Created person 'Alice' with id={alice_id}")
    
    bob_id = insert_entity(
        conn,
        entity_type="person",
        string_attrs={"name": "Bob", "email": "bob@example.com", "city": "San Francisco"},
        int_attrs={"age": 25, "score": 88}
    )
    print(f"  Created person 'Bob' with id={bob_id}")
    
    laptop_id = insert_entity(
        conn,
        entity_type="product",
        string_attrs={"name": "Laptop Pro", "brand": "TechCorp", "category": "Electronics"},
        int_attrs={"price": 1299, "stock": 50}
    )
    print(f"  Created product 'Laptop Pro' with id={laptop_id}")
    
    print()
    
    # Query entities
    print("Querying entities...")
    print()
    
    for entity_id in [alice_id, bob_id, laptop_id]:
        entity = get_entity(conn, entity_id)
        if entity:
            print(f"  Entity {entity['id']} ({entity['entity_type']}):")
            print(f"    Created: {entity['created_at']}")
            print(f"    Attributes:")
            for name, value in sorted(entity["attributes"].items()):
                print(f"      - {name}: {value}")
            print()
    
    # Close connection
    conn.close()
    print(f"Database saved to: {DB_PATH}")
    print("Done!")


if __name__ == "__main__":
    main()
