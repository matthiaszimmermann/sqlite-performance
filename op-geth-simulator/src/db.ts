import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { Database } from "bun:sqlite"
import { logQueryWarning } from "./logger.js"
import type { Entity, PendingEntity } from "./types.js"

// Connection pool configuration
const DEFAULT_POOL_SIZE = 4 // Number of read connections
let poolSize = DEFAULT_POOL_SIZE

// Connection pool
let readPool: Database[] = []
let writeDb: Database | null = null
let poolInitialized = false
let currentReadIndex = 0

function logDbOperation(operation: string, duration: number): void {
  const message = `[DB] ${operation} - ${duration.toFixed(2)}ms`
  console.log(message)

  // Warn if any query takes more than 200ms (warnings are logged to file)
  if (duration > 200) {
    logQueryWarning(operation, duration)
  }
}

export function initDatabase(
  dbPath: string = "op-geth-sim.db",
  poolSizeOverride?: number,
): Database {
  if (poolInitialized && writeDb) {
    return writeDb
  }

  if (poolSizeOverride !== undefined) {
    poolSize = poolSizeOverride
  }

  // Read and execute schema - find schema.sql relative to this file
  const __filename = fileURLToPath(import.meta.url)
  const __dirname = dirname(__filename)
  const schemaPath = join(__dirname, "../../", "arkiv.schema.sql")
  const schema = `${readFileSync(schemaPath, "utf-8")}\n
  CREATE TABLE IF NOT EXISTS entity_receipts (
    id TEXT NOT NULL PRIMARY KEY,
    entity_key TEXT NOT NULL,
    created_at_block INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_entity_receipts_id ON entity_receipts(id);
  `

  // Initialize write connection (single connection for writes)
  // mode=rwc (read-write-create, default for write connections)
  writeDb = new Database(dbPath)

  // Set pragmas for write connection
  writeDb.run("PRAGMA journal_mode = WAL") // _journal_mode=WAL
  writeDb.run("PRAGMA busy_timeout = 11000") // _busy_timeout=11000 (11 seconds)
  writeDb.run("PRAGMA auto_vacuum = incremental") // _auto_vacuum=incremental
  writeDb.run("PRAGMA foreign_keys = OFF") // _foreign_keys=true
  writeDb.run("PRAGMA cache_size = 100000") // _cache_size=1000000000 (in pages)
  // Note: _txlock=immediate - transactions will use BEGIN IMMEDIATE for immediate locking

  writeDb.run(schema)

  // Initialize read pool (multiple connections for concurrent reads)
  readPool = []
  for (let i = 0; i < poolSize; i++) {
    // Configure read connections with optimized settings
    // bun:sqlite doesn't support readonly option in constructor, we'll use pragma instead
    const readDb = new Database(dbPath)

    // Set pragmas for read connections
    readDb.run("PRAGMA journal_mode = WAL") // _journal_mode=WAL
    readDb.run("PRAGMA busy_timeout = 11000") // _busy_timeout=11000 (11 seconds)
    readDb.run("PRAGMA auto_vacuum = incremental") // _auto_vacuum=incremental
    readDb.run("PRAGMA foreign_keys = OFF") // _foreign_keys=true
    readDb.run("PRAGMA cache_size = 100000") // _cache_size=1000000000 (in pages)
    // Note: _txlock=deferred is the default transaction locking mode in SQLite

    readPool.push(readDb)
  }

  poolInitialized = true
  console.log(`Database pool initialized: ${poolSize} read connections, 1 write connection`)

  return writeDb
}

/**
 * Get a read connection from the pool (round-robin)
 */
function getReadConnection(): Database {
  if (!poolInitialized || readPool.length === 0) {
    throw new Error("Database pool not initialized. Call initDatabase() first.")
  }

  // Round-robin selection
  const connection = readPool[currentReadIndex]
  currentReadIndex = (currentReadIndex + 1) % readPool.length
  return connection
}

/**
 * Get the write connection (single connection for all writes)
 */
function getWriteConnection(): Database {
  if (!poolInitialized || !writeDb) {
    throw new Error("Database pool not initialized. Call initDatabase() first.")
  }
  return writeDb
}

/**
 * Execute a transaction with immediate locking (_txlock=immediate)
 * This ensures the transaction acquires a write lock immediately
 */
function immediateTransaction<T>(database: Database, fn: () => T): T {
  try {
    // Begin immediate transaction (acquires write lock immediately)
    database.run("BEGIN IMMEDIATE TRANSACTION")
    const result = fn()
    database.run("COMMIT")
    return result
  } catch (error) {
    database.run("ROLLBACK")
    throw error
  }
}

/**
 * Get a database connection (read by default, use getWriteConnection for writes)
 * @deprecated Use getReadConnection() or getWriteConnection() instead
 */
export function getDatabase(): Database {
  return getReadConnection()
}

export function closeDatabase(): void {
  // Close all read connections
  for (const db of readPool) {
    try {
      db.close()
    } catch (error) {
      console.error("Error closing read connection:", error)
    }
  }
  readPool = []

  // Close write connection
  if (writeDb) {
    try {
      writeDb.close()
    } catch (error) {
      console.error("Error closing write connection:", error)
    }
    writeDb = null
  }

  poolInitialized = false
  currentReadIndex = 0
}

export function insertEntity(entity: Entity): void {
  const startTime = performance.now()
  const database = getWriteConnection()

  // Convert entity key from string to BLOB
  const entityKeyBuffer = Buffer.from(entity.key, "utf-8")

  // Convert payload
  const payload =
    typeof entity.payload === "string"
      ? Buffer.from(entity.payload, "base64")
      : entity.payload || null

  // Ensure owner_address is included in string annotations
  const stringAnnotations = entity.stringAnnotations ? { ...entity.stringAnnotations } : {}
  if (entity.ownerAddress) {
    stringAnnotations.ownerAddress = entity.ownerAddress
  }

  // Serialize annotations to JSON for storage in payloads table
  const stringAttributesJson =
    Object.keys(stringAnnotations).length > 0 ? JSON.stringify(stringAnnotations) : null
  const numericAttributesJson = entity.numericAnnotations
    ? JSON.stringify(entity.numericAnnotations)
    : null

  // Insert into payloads table
  // from_block uses lastModifiedAtBlock, to_block uses expiresAt
  const insertPayloadStmt = database.prepare(`
    INSERT INTO payloads (
      entity_key, from_block, to_block, payload, content_type,
      string_attributes, numeric_attributes
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
  `)

  insertPayloadStmt.run(
    entityKeyBuffer,
    entity.lastModifiedAtBlock,
    entity.expiresAt,
    payload,
    entity.contentType,
    stringAttributesJson,
    numericAttributesJson,
  )

  // Insert string attributes into separate table for querying
  if (Object.keys(stringAnnotations).length > 0) {
    const insertStringAttrStmt = database.prepare(`
      INSERT INTO string_attributes (
        entity_key, from_block, to_block, key, value
      ) VALUES (?, ?, ?, ?, ?)
    `)

    for (const [key, value] of Object.entries(stringAnnotations)) {
      insertStringAttrStmt.run(
        entityKeyBuffer,
        entity.lastModifiedAtBlock,
        entity.expiresAt,
        key,
        value,
      )
    }
  }

  // Insert numeric attributes into separate table for querying
  if (entity.numericAnnotations) {
    const insertNumericAttrStmt = database.prepare(`
      INSERT INTO numeric_attributes (
        entity_key, from_block, to_block, key, value
      ) VALUES (?, ?, ?, ?, ?)
    `)

    for (const [key, value] of Object.entries(entity.numericAnnotations)) {
      insertNumericAttrStmt.run(
        entityKeyBuffer,
        entity.lastModifiedAtBlock,
        entity.expiresAt,
        key,
        value,
      )
    }
  }

  const duration = performance.now() - startTime
  logDbOperation(`insertEntity(key=${entity.key})`, duration)
}

export function insertEntitiesBatch(entities: PendingEntity[], blockNumber: number = 0): void {
  const startTime = performance.now()
  const database = getWriteConnection()
  immediateTransaction(database, () => {
    const insertReceiptStmt = database.prepare(`
      INSERT INTO entity_receipts (id, entity_key, created_at_block)
      VALUES (?, ?, ?)
    `)

    for (const entity of entities) {
      entity.createdAtBlock = blockNumber
      entity.lastModifiedAtBlock = blockNumber
      
      insertEntity(entity)
      // Store receipt for this entity
      insertReceiptStmt.run(entity.id, entity.key, entity.createdAtBlock)
    }
  })
  updateBlockNumber(blockNumber)
  const duration = performance.now() - startTime
  logDbOperation(`insertEntitiesBatch(count=${entities.length})`, duration)
}

export function removeExpiredEntities(blockNumber: number): void {
  const startTime = performance.now()
  const database = getWriteConnection()
  immediateTransaction(database, () => {
    database.prepare("DELETE FROM payloads WHERE to_block = ?").run(blockNumber)
  })
  const duration = performance.now() - startTime
  logDbOperation(`removeExpiredEntities(blockNumber=${blockNumber})`, duration)
}

export function getEntityByKey(key: string): Entity | null {
  const startTime = performance.now()
  const database = getReadConnection()

  // Convert key to BLOB for querying
  const entityKeyBuffer = Buffer.from(key, "utf-8")

  const getEntityStmt = database.prepare(`
    SELECT * FROM payloads
    WHERE entity_key = ?
    ORDER BY from_block DESC
    LIMIT 1
  `)

  const row = getEntityStmt.get(entityKeyBuffer) as any
  if (!row) {
    const duration = performance.now() - startTime
    logDbOperation(`getEntityByKey(key=${key}) - not found`, duration)
    return null
  }

  // Parse annotations from JSON
  let stringAnnotations: Record<string, string> | undefined
  let numericAnnotations: Record<string, number> | undefined

  if (row.string_attributes) {
    try {
      stringAnnotations = JSON.parse(row.string_attributes)
    } catch (e) {
      console.warn(`Failed to parse string_attributes for key ${key}:`, e)
    }
  }

  if (row.numeric_attributes) {
    try {
      numericAnnotations = JSON.parse(row.numeric_attributes)
    } catch (e) {
      console.warn(`Failed to parse numeric_attributes for key ${key}:`, e)
    }
  }

  // Convert entity_key BLOB back to string
  const entityKey = row.entity_key instanceof Buffer ? row.entity_key.toString("utf-8") : key

  // Try to get owner_address from string_attributes if it exists
  const ownerAddress = stringAnnotations?.ownerAddress || ""

  const duration = performance.now() - startTime
  logDbOperation(`getEntityByKey(key=${key})`, duration)

  return {
    key: entityKey,
    expiresAt: row.to_block,
    payload: row.payload,
    contentType: row.content_type,
    createdAtBlock: row.from_block, // Use from_block as both created and modified
    lastModifiedAtBlock: row.from_block,
    deleted: false, // New schema doesn't track deleted
    transactionIndexInBlock: 0, // New schema doesn't track transaction index
    operationIndexInTransaction: 0, // New schema doesn't track operation index
    ownerAddress: ownerAddress,
    stringAnnotations: stringAnnotations,
    numericAnnotations: numericAnnotations,
  }
}

export function queryEntities(
  ownerAddress?: string,
  stringAnnotations?: Record<string, string>,
  numericAnnotations?: Record<string, number>,
  limit: number = 100,
  offset: number = 0,
  withAnnotations: boolean = false,
): Entity[] {
  const startTime = performance.now()
  const database = getReadConnection()

  // Build query using payloads table
  // We need to get distinct entity_keys, taking the most recent version (highest from_block)
  let query = `
    SELECT p.*
    FROM payloads p
    INNER JOIN (
      SELECT entity_key, MAX(from_block) as max_block
      FROM payloads
      GROUP BY entity_key
    ) latest ON p.entity_key = latest.entity_key AND p.from_block = latest.max_block
    WHERE 1=1
  `
  const params: any[] = []

  // Filter by owner_address if provided (stored in string_attributes)
  if (ownerAddress) {
    query += ` AND EXISTS (
      SELECT 1 FROM string_attributes sa
      WHERE sa.entity_key = p.entity_key
        AND sa.from_block = p.from_block
        AND sa.key = 'ownerAddress'
        AND sa.value = ?
    )`
    params.push(ownerAddress)
  }

  // Filter by string annotations
  if (stringAnnotations && Object.keys(stringAnnotations).length > 0) {
    for (const [key, value] of Object.entries(stringAnnotations)) {
      query += ` AND EXISTS (
        SELECT 1 FROM string_attributes sa
        WHERE sa.entity_key = p.entity_key
          AND sa.from_block = p.from_block
          AND sa.key = ?
          AND sa.value = ?
      )`
      params.push(key, value)
    }
  }

  // Filter by numeric annotations
  if (numericAnnotations && Object.keys(numericAnnotations).length > 0) {
    for (const [key, value] of Object.entries(numericAnnotations)) {
      query += ` AND EXISTS (
        SELECT 1 FROM numeric_attributes na
        WHERE na.entity_key = p.entity_key
          AND na.from_block = p.from_block
          AND na.key = ?
          AND na.value = ?
      )`
      params.push(key, value)
    }
  }

  query += `
    ORDER BY p.from_block DESC
    LIMIT ? OFFSET ?
  `
  params.push(limit, offset)

  const stmt = database.prepare(query)
  const rows = stmt.all(...params) as any[]

  const duration = performance.now() - startTime
  logDbOperation(
    `queryEntities(limit=${limit}, offset=${offset}, row count=${rows.length})`,
    duration,
  )

  // For each entity, parse annotations and build Entity object
  const result = rows.map((row) => {
    // Parse annotations from JSON
    let stringAnnotations: Record<string, string> | undefined
    let numericAnnotations: Record<string, number> | undefined

    if (withAnnotations || row.string_attributes) {
      if (row.string_attributes) {
        try {
          stringAnnotations = JSON.parse(row.string_attributes)
        } catch (e) {
          console.warn(`Failed to parse string_attributes:`, e)
        }
      }
    }

    if (withAnnotations || row.numeric_attributes) {
      if (row.numeric_attributes) {
        try {
          numericAnnotations = JSON.parse(row.numeric_attributes)
        } catch (e) {
          console.warn(`Failed to parse numeric_attributes:`, e)
        }
      }
    }

    // Convert entity_key BLOB to string
    const entityKey =
      row.entity_key instanceof Buffer ? row.entity_key.toString("utf-8") : String(row.entity_key)

    // Get owner_address from annotations
    const ownerAddr = stringAnnotations?.ownerAddress || ""

    return {
      key: entityKey,
      expiresAt: row.to_block,
      payload: row.payload,
      contentType: row.content_type,
      createdAtBlock: row.from_block,
      lastModifiedAtBlock: row.from_block,
      deleted: false,
      transactionIndexInBlock: 0,
      operationIndexInTransaction: 0,
      ownerAddress: ownerAddr,
      stringAnnotations: stringAnnotations,
      numericAnnotations: numericAnnotations,
    }
  })

  return result
}

export function getCurrentBlockNumber(): number {
  const database = getReadConnection()
  const stmt = database.prepare("SELECT block FROM last_block WHERE id = 1")
  const result = stmt.get() as { block: number }
  return result.block
}

export function updateBlockNumber(blockNumber: number): void {
  const database = getWriteConnection()
  database.prepare("UPDATE last_block SET block = ? WHERE id = 1").run(blockNumber)
}

export function countEntities(): number {
  const startTime = performance.now()
  const database = getReadConnection()
  // Count distinct entity_keys in payloads table
  const stmt = database.prepare("SELECT COUNT(DISTINCT entity_key) as count FROM payloads")
  const result = stmt.get() as { count: number }
  const duration = performance.now() - startTime
  logDbOperation(`countEntities(count=${result.count})`, duration)
  return result.count
}

export function getEntityBasicInfo(key: string): { key: string; createdAtBlock: number } | null {
  const startTime = performance.now()
  const database = getReadConnection()

  // Convert key to BLOB for querying
  const entityKeyBuffer = Buffer.from(key, "utf-8")

  const stmt = database.prepare(`
    SELECT entity_key, from_block
    FROM payloads
    WHERE entity_key = ?
    ORDER BY from_block DESC
    LIMIT 1
  `)

  const row = stmt.get(entityKeyBuffer) as { entity_key: Buffer; from_block: number } | undefined
  if (!row) {
    const duration = performance.now() - startTime
    logDbOperation(`getEntityBasicInfo(key=${key}) - not found`, duration)
    return null
  }

  const duration = performance.now() - startTime
  logDbOperation(`getEntityBasicInfo(key=${key})`, duration)

  return {
    key: key,
    createdAtBlock: row.from_block,
  }
}

export function getReceiptById(id: string): {
  id: string
  key: string
  createdAtBlock: number
} | null {
  const startTime = performance.now()
  const database = getReadConnection()

  const stmt = database.prepare(`
    SELECT id, entity_key, created_at_block
    FROM entity_receipts
    WHERE id = ?
  `)

  const row = stmt.get(id) as
    | { id: string; entity_key: string; created_at_block: number }
    | undefined

  if (!row) {
    const duration = performance.now() - startTime
    logDbOperation(`getReceiptById(id=${id}) - not found`, duration)
    return null
  }

  const duration = performance.now() - startTime
  logDbOperation(`getReceiptById(id=${id})`, duration)

  return {
    id: row.id,
    key: row.entity_key,
    createdAtBlock: row.created_at_block,
  }
}

export function cleanAllData(): void {
  const database = getWriteConnection()
  immediateTransaction(database, () => {
    // Delete all attributes
    database.prepare("DELETE FROM string_attributes").run()
    database.prepare("DELETE FROM numeric_attributes").run()
    // Delete all payloads
    database.prepare("DELETE FROM payloads").run()
    // Delete all receipts
    database.prepare("DELETE FROM entity_receipts").run()
    // Reset last_block
    database.prepare("DELETE FROM last_block").run()
  })
}

export function vacuumDatabase(): void {
  const database = getWriteConnection()

  // When using WAL mode, we need to checkpoint and then switch modes for effective VACUUM
  // Checkpoint WAL file to merge it into the main database
  try {
    database.run("PRAGMA wal_checkpoint(TRUNCATE)")
  } catch (error) {
    // If checkpoint fails, continue anyway
    console.warn("WAL checkpoint warning:", error)
  }

  // Temporarily switch to DELETE mode for more effective VACUUM
  // This ensures all space is reclaimed
  const originalModeResult = database.prepare("PRAGMA journal_mode").get() as { journal_mode: string } | undefined
  const originalMode = originalModeResult?.journal_mode || "WAL"
  try {
    if (originalMode.toUpperCase() === "WAL") {
      database.run("PRAGMA journal_mode = DELETE")
    }

    // Now run VACUUM to reclaim space
    database.run("VACUUM")

    // Switch back to original mode
    if (originalMode.toUpperCase() === "WAL") {
      database.run("PRAGMA journal_mode = WAL")
    }
  } catch (error) {
    // Try to restore original mode even if VACUUM failed
    if (originalMode.toUpperCase() === "WAL") {
      try {
        database.run("PRAGMA journal_mode = WAL")
      } catch {
        // Ignore restore errors
      }
    }
    throw error
  }
}
