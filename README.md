# SQLite Performance

A project for exploring SQLite performance patterns in Python, featuring an EVA (Entity-Value-Attribute) implementation.

## Setup

### Dev Container

This project uses a VS Code dev container with:
- Python 3.12
- SQLite 3 CLI tools (`sqlite3`, `libsqlite3-dev`)
- [uv](https://github.com/astral-sh/uv) for fast Python package management

To get started, open this project in VS Code and select **"Reopen in Container"** when prompted.

### Python Environment

Dependencies are managed with `uv`. After opening the dev container, sync dependencies:

```bash
uv sync --dev
```

This installs:
- `pytest` - testing framework
- `pytest-cov` - coverage reporting
- `ruff` - linter and formatter

## Project Structure

```
├── src/db/
│   ├── __init__.py
│   └── eva.py             # EVA pattern implementation + demo
├── tests/
│   └── test_eva.py        # Tests for EVA module
├── pyproject.toml         # Project configuration
└── .python-version        # Python version (3.12)
```

## EVA (Entity-Value-Attribute) Pattern

The EVA pattern stores entities with flexible attributes in separate tables:

- **entity** - Core entity table with `id`, `entity_type`, and `created_at`
- **attribute_string** - String attributes (name, email, etc.)
- **attribute_int** - Integer attributes (age, price, etc.)

This pattern is useful when entities have varying attributes that aren't known at design time.

## Usage

### Run the EVA Demo

```bash
uv run python -m db.eva
```

This creates a `demo.db` file with sample entities (people and products).

### Run Tests

```bash
# Run all tests
uv run pytest -v

# Run specific test file
uv run pytest tests/test_eva.py -v
```

## SQLite CLI Tools

### View Database Schema

```bash
# Show all table schemas
sqlite3 demo.db ".schema"

# List all tables
sqlite3 demo.db ".tables"

# Show schema for a specific table
sqlite3 demo.db ".schema entity"
```

### Query the Database

```bash
# Interactive mode
sqlite3 demo.db

# One-off query with formatted output
sqlite3 demo.db -header -column "SELECT * FROM entity;"

# Query string attributes
sqlite3 demo.db -header -column "SELECT * FROM attribute_string;"

# Query integer attributes  
sqlite3 demo.db -header -column "SELECT * FROM attribute_int;"
```

### Example Queries

```bash
# Get all attributes for entity ID 1 (Alice)
sqlite3 demo.db -header -column \
  "SELECT e.entity_type, a.attribute_name, a.attribute_value 
   FROM entity e 
   JOIN attribute_string a ON e.id = a.entity_id 
   WHERE e.id = 1;"

# Find all people over age 25
sqlite3 demo.db -header -column \
  "SELECT e.id, s.attribute_value as name, i.attribute_value as age
   FROM entity e
   JOIN attribute_string s ON e.id = s.entity_id AND s.attribute_name = 'name'
   JOIN attribute_int i ON e.id = i.entity_id AND i.attribute_name = 'age'
   WHERE e.entity_type = 'person' AND i.attribute_value > 25;"

# Count entities by type
sqlite3 demo.db -header -column \
  "SELECT entity_type, COUNT(*) as count FROM entity GROUP BY entity_type;"
```

### Useful SQLite CLI Commands

| Command | Description |
|---------|-------------|
| `.schema` | Show all table schemas |
| `.tables` | List all tables |
| `.headers on` | Enable column headers |
| `.mode column` | Pretty column output |
| `.mode csv` | CSV output |
| `.mode json` | JSON output |
| `.indexes` | List all indexes |
| `.quit` | Exit |

## License

MIT