# op-geth-sim

A Hono.js server with SQLite that simulates op-geth block processing. Entities are collected in memory and written to the database in batches every 2 seconds, simulating block processing.

## Features

- **Write Entity**: Queue entities for processing (doesn't write immediately to DB)
- **Get Entity**: Retrieve an entity by its key
- **Query Entities**: Search entities by attributes (string/numeric annotations, owner address)
- **Count Entities**: Get total count of all entities in the database

## Architecture

The server uses an in-memory queue to collect entity writes. A background process runs every 2 seconds to simulate block processing, writing all queued entities to SQLite in a batch.

## Setup

1. Install dependencies:
```bash
npm install
```

2. Build the project:
```bash
npm run build
```

3. Start the server:
```bash
npm start
```

Or run in development mode with auto-reload:
```bash
npm run dev
```

The server will start on port 3000 (or the port specified in the `PORT` environment variable).

## API Endpoints

### 1. Write Entity
**POST** `/entities`

Queue an entity for processing. The entity will be written to the database during the next block processing cycle (every 2 seconds).

**Request Body:**
```json
{
  "key": "entity-key-123",
  "expiresAt": 1234567890,
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

## Database Schema

The database uses the schema defined in `schema.sql`, which includes:
- `entities` table for storing entity data
- `string_annotations` table for string metadata
- `numeric_annotations` table for numeric metadata
- `processing_status` table for tracking block processing
- `schema_versions` table for schema versioning

## Block Processing

Every 2 seconds, the block processor:
1. Collects all entities from the in-memory queue
2. Assigns block numbers, transaction indices, and operation indices
3. Writes all entities to SQLite in a batch
4. Clears the queue

This simulates how op-geth processes blocks and writes data to the database.

## Testing

A comprehensive test script is included to verify all endpoints and functionality.

**Prerequisites:** Make sure the server is running (in a separate terminal).

Run the test script:
```bash
npm test
```

Or directly:
```bash
tsx src/test.ts
```

The test script will:
- Check server health
- Write multiple entities with various attributes
- Wait for block processing
- Read entities by key
- Query entities by owner address, annotations, and combinations
- Test pagination
- Count entities
- Handle edge cases (non-existent entities)

You can customize the server URL by setting the `SERVER_URL` environment variable:
```bash
SERVER_URL=http://localhost:3000 npm test
```

## CLI Tool

A command-line tool is available for adding entities with random payloads and cleaning data. **The CLI writes directly to the database** (no server required), making it ideal for bulk operations.

### Add Entities

Add N entities with random payload sizes and M attributes:

```bash
npm run cli add <count> [attributes] [max-size]
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

**Features:**
- Writes directly to the database (no server needed)
- Batch processing: Inserts in batches of 1000 entities for optimal performance
- Generates entities with random payload sizes between 0.5KB and max-size
- Uses random owner addresses
- Shows real-time progress and performance metrics (entities/second)

**Examples:**
```bash
# Add 100 entities with default 10 attributes, max 120KB payload
npm run cli add 100

# Add 100 entities with 20 attributes, max 120KB payload
npm run cli add 100 20

# Add 1000 entities with 50 attributes, max 50KB payload
npm run cli add 1000 50 50

# Add 100000 entities with 100 attributes, max 10KB payload (bulk operation)
npm run cli add 100000 100 10

# Add 10000 entities with 20 attributes, max 5KB payload
npm run cli add 10000 20 5
```

### Clean All Data

Remove all entities and annotations from the database:

```bash
npm run cli clean
```

**Note:** This permanently deletes all data. Use with caution! The CLI writes directly to the database, so no server is required.

### Custom Database Path

You can specify a different database file:

```bash
DB_PATH=my-database.db npm run cli add 100
```

### Help

View usage information:

```bash
npm run cli help
```

## Visualization

A Python script is available to visualize replication performance data from the CSV log file.

### Prerequisites

Install Python dependencies:

```bash
pip install -r requirements-visualization.txt
```

### Usage

Generate charts from the replication log:

```bash
python3 visualize_replication.py
```

Or specify custom CSV file and output image:

```bash
python3 visualize_replication.py replication_log.csv output_charts.png
```

The script generates two charts:

1. **Block Performance Over Time**: Shows read/write times for each block, with X-axis labeled by payload count
2. **Performance vs Database Size**: Shows how read/write times correlate with the growing database file size

The output image (`replication_charts.png`) will be saved in the same directory.

