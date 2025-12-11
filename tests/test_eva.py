"""Tests for EVA (Entity-Value-Attribute) module."""

import sqlite3

import pytest

from db.eva import (
    create_eva_schema,
    get_entity,
    insert_entity,
)


@pytest.fixture
def conn():
    """Create an in-memory database with EVA schema for testing."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_eva_schema(conn)
    yield conn
    conn.close()


class TestInsertEntity:
    """Tests for insert_entity function."""

    def test_insert_entity_returns_id(self, conn):
        """Should return the ID of the created entity."""
        entity_id = insert_entity(conn, entity_type="person")
        assert entity_id == 1

    def test_insert_multiple_entities_increments_id(self, conn):
        """Should increment IDs for multiple entities."""
        id1 = insert_entity(conn, entity_type="person")
        id2 = insert_entity(conn, entity_type="person")
        id3 = insert_entity(conn, entity_type="product")
        
        assert id1 == 1
        assert id2 == 2
        assert id3 == 3

    def test_insert_entity_with_string_attributes(self, conn):
        """Should insert string attributes."""
        entity_id = insert_entity(
            conn,
            entity_type="person",
            string_attrs={"name": "Alice", "email": "alice@example.com"}
        )
        
        cursor = conn.cursor()
        cursor.execute(
            "SELECT attribute_name, attribute_value FROM attribute_string WHERE entity_id = ?",
            (entity_id,)
        )
        attrs = {row["attribute_name"]: row["attribute_value"] for row in cursor.fetchall()}
        
        assert attrs == {"name": "Alice", "email": "alice@example.com"}

    def test_insert_entity_with_int_attributes(self, conn):
        """Should insert integer attributes."""
        entity_id = insert_entity(
            conn,
            entity_type="person",
            int_attrs={"age": 30, "score": 95}
        )
        
        cursor = conn.cursor()
        cursor.execute(
            "SELECT attribute_name, attribute_value FROM attribute_int WHERE entity_id = ?",
            (entity_id,)
        )
        attrs = {row["attribute_name"]: row["attribute_value"] for row in cursor.fetchall()}
        
        assert attrs == {"age": 30, "score": 95}


class TestGetEntity:
    """Tests for get_entity function."""

    def test_get_entity_returns_none_for_missing(self, conn):
        """Should return None if entity does not exist."""
        result = get_entity(conn, 999)
        assert result is None

    def test_get_entity_returns_entity_type(self, conn):
        """Should return the entity type."""
        entity_id = insert_entity(conn, entity_type="person")
        result = get_entity(conn, entity_id)
        
        assert result["entity_type"] == "person"

    def test_get_entity_returns_id(self, conn):
        """Should return the entity ID."""
        entity_id = insert_entity(conn, entity_type="product")
        result = get_entity(conn, entity_id)
        
        assert result["id"] == entity_id

    def test_get_entity_returns_string_attributes(self, conn):
        """Should return all string attributes."""
        entity_id = insert_entity(
            conn,
            entity_type="person",
            string_attrs={"name": "Bob", "city": "NYC"}
        )
        result = get_entity(conn, entity_id)
        
        assert result["attributes"]["name"] == "Bob"
        assert result["attributes"]["city"] == "NYC"

    def test_get_entity_returns_int_attributes(self, conn):
        """Should return all integer attributes."""
        entity_id = insert_entity(
            conn,
            entity_type="product",
            int_attrs={"price": 100, "stock": 50}
        )
        result = get_entity(conn, entity_id)
        
        assert result["attributes"]["price"] == 100
        assert result["attributes"]["stock"] == 50

    def test_get_entity_returns_mixed_attributes(self, conn):
        """Should return both string and integer attributes."""
        entity_id = insert_entity(
            conn,
            entity_type="person",
            string_attrs={"name": "Charlie", "email": "charlie@test.com"},
            int_attrs={"age": 25, "level": 5}
        )
        result = get_entity(conn, entity_id)
        
        assert result["attributes"] == {
            "name": "Charlie",
            "email": "charlie@test.com",
            "age": 25,
            "level": 5
        }

    def test_get_entity_has_created_at(self, conn):
        """Should include created_at timestamp."""
        entity_id = insert_entity(conn, entity_type="person")
        result = get_entity(conn, entity_id)
        
        assert "created_at" in result
        assert result["created_at"] is not None
