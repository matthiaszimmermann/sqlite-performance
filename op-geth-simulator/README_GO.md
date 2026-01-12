# op-geth-simulator (Go Implementation)

A Go reimplementation of the op-geth-simulator that uses the `sqlite-bitmap-store` interface instead of direct SQLite access. This maintains the same logic and functionality as the TypeScript version but leverages the bitmap store for efficient data management.

## Features

- **Write Entity**: Queue entities for processing (doesn't write immediately to DB)
- **Get Entity**: Retrieve an entity by its key
- **Query Entities**: Search entities by attributes (string/numeric annotations, owner address)
- **Count Entities**: Get total count of all entities in the database
- **Block Processing**: Background process that processes queued entities every 2 seconds

## Architecture

The server uses an in-memory queue to collect entity writes. A background process runs every 2 seconds to simulate block processing, writing all queued entities to the store using the `sqlite-bitmap-store` interface.

## Setup

1. Install Go dependencies:
```bash
go mod download
```

2. Build the project:
```bash
go build -o op-geth-simulator
```

3. Start the server:
```bash
./op-geth-simulator
```

Or run directly:
```bash
go run .
```

The server will start on port 3000 (or the port specified via `--port` flag or `PORT` environment variable).

## Command Line Options

- `--db-path`: Database file path (default: `op-geth-sim.db`)
- `--testname`: Test name for logging (optional)
- `--port`: Server port (default: 3000)

Example:
```bash
go run . --db-path=my-db.db --testname=test1 --port=8080
```

## API Endpoints

### 1. Write Entity
**POST** `/entities`

Queue an entity for processing. The entity will be written to the database during the next block processing cycle (every 2 seconds).

**Request Body:**
```json
{
  "key": "entity-key-123",
  "expiresIn": 100,
  "payload": "base64-encoded-payload",
  "contentType": "application/json",
  "ownerAddress": "0x1234...",
  "deleted": false,
  "stringAnnotations": {
    "tag": "important",
    "category": "data"
  },
  "numericAnnotations": {
    "priority": 5,
    "version": 1
  }
}
```

**Response:**
```json
{
  "success": true,
  "id": "unique-queue-id",
  "message": "Entity queued for processing",
  "queueSize": 1
}
```

### 2. Get Entity by Key
**GET** `/entities/:key`

Retrieve the latest version of an entity by its key.

**Response:**
```json
{
  "key": "entity-key-123",
  "expiresAt": 1234567890,
  "payload": "base64-encoded-payload",
  "contentType": "application/json",
  "createdAtBlock": 1,
  "lastModifiedAtBlock": 1,
  "deleted": false,
  "transactionIndexInBlock": 0,
  "operationIndexInTransaction": 0,
  "ownerAddress": "0x1234...",
  "stringAnnotations": {
    "tag": "important",
    "category": "data"
  },
  "numericAnnotations": {
    "priority": 5,
    "version": 1
  }
}
```

### 3. Query Entities
**POST** `/entities/query`

Search for entities by attributes.

**Request Body:**
```json
{
  "ownerAddress": "0x1234...",
  "stringAnnotations": {
    "tag": "important"
  },
  "numericAnnotations": {
    "priority": 5
  },
  "limit": 100,
  "offset": 0
}
```

**Response:**
```json
{
  "entities": [...],
  "count": 10
}
```

### 4. Count Entities
**GET** `/entities/count`

Get the total count of all non-deleted entities.

**Response:**
```json
{
  "count": 42
}
```

### Health Check
**GET** `/health`

Check server status and queue size.

**Response:**
```json
{
  "status": "ok",
  "queueSize": 5
}
```

## CLI Tool

A command-line tool is available for adding entities with random payloads and cleaning data. The CLI writes directly to the database (no server required), making it ideal for bulk operations.

### Add Entities

Add N entities with random payload sizes and M attributes:

```bash
go run . cli add <count> [attributes] [max-size]
```

**Parameters:**
- `count`: Number of entities to add (required)
- `attributes`: Number of attributes per entity (optional, default: 10)
  - Half will be string attributes with constant keys (`attr_str_0`, `attr_str_1`, ...)
  - Half will be numeric attributes with constant keys (`attr_num_0`, `attr_num_1`, ...)
  - String attributes: Random values from a predefined list of 20 words
  - Numeric attributes: Random values from 0 to 10
- `max-size`: Maximum payload size in KB (optional, default: 120)
  - Payload sizes will be random between 0.5KB and max-size

**Examples:**
```bash
# Add 100 entities with default 10 attributes, max 120KB payload
go run . cli add 100

# Add 100 entities with 20 attributes, max 120KB payload
go run . cli add 100 20

# Add 1000 entities with 50 attributes, max 50KB payload
go run . cli add 1000 50 50
```

### Clean All Data

Remove all entities and annotations from the database:

```bash
go run . cli clean
```

**Note:** This permanently deletes all data. Use with caution!

### Custom Database Path

You can specify a different database file:

```bash
DB_PATH=my-database.db go run . cli add 100
```

## Differences from TypeScript Version

1. **Store Interface**: Uses `sqlite-bitmap-store/store` interface instead of direct SQLite access
2. **Language**: Go instead of TypeScript/Bun
3. **HTTP Framework**: Gorilla Mux instead of Hono
4. **Concurrency**: Uses Go's native goroutines and channels for concurrency

## Store Interface

The implementation expects the `sqlite-bitmap-store/store` package to provide the following interface:

```go
type Store interface {
    NewStore(dbPath string) (*Store, error)
    Close() error
    AddPayload(entityKey []byte, payload []byte, attributes map[string]interface{}, fromBlock int, toBlock int) error
    GetPayload(entityKey []byte, blockNumber int) ([]byte, map[string]interface{}, error)
    QueryPayloads(attributes map[string]interface{}, blockNumber int, limit int, offset int) ([]QueryResult, error)
    RemoveExpired(blockNumber int) error
    GetCurrentBlock() (int, error)
    SetCurrentBlock(blockNumber int) error
    Count() (int, error)
    Clear() error
}

type QueryResult struct {
    EntityKey  []byte
    Payload    []byte
    Attributes map[string]interface{}
}
```

**Note:** The actual interface may differ. Please refer to the `sqlite-bitmap-store` documentation and adjust the wrapper in `store.go` accordingly.

## Logging

The application generates several log files:

- `performance.log`: Performance warnings and slow operations
- `query.log`: Query performance metrics
- `processing.log`: Block processing metrics

## Block Processing

Every 2 seconds, the block processor:
1. Collects all entities from the in-memory queue
2. Assigns block numbers, transaction indices, and operation indices
3. Writes all entities to the store using the bitmap store interface
4. Clears the queue

This simulates how op-geth processes blocks and writes data to the database.

