import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { Database } from "bun:sqlite"
import { createPool, type Pool, type PoolConnection } from "mysql2/promise"
import { logQueryWarning, logQuery } from "./logger.js"
import type { Entity, PendingEntity } from "./types.js"

// Connection pool configuration
const DEFAULT_POOL_SIZE = 4 // Number of read connections
let poolSize = DEFAULT_POOL_SIZE

type DbEngine = "sqlite" | "mysql"
const dbEngine: DbEngine = (process.env.DB_ENGINE || "sqlite").toLowerCase() === "mysql" ? "mysql" : "sqlite"

// SQLite connection pool
let sqliteReadPool: Database[] = []
let sqliteWriteDb: Database | null = null

// MySQL pool
let mysqlPool: Pool | null = null

let poolInitialized = false
let currentReadIndex = 0

async function ensureLastBlockInitialized(conn?: PoolConnection): Promise<void> {
  if (dbEngine === "sqlite") {
    if (!sqliteWriteDb) {
      throw new Error("SQLite DB not initialized. Call initDatabase() first.")
    }
    sqliteWriteDb.prepare("INSERT OR IGNORE INTO last_block (id, block) VALUES (1, 0)").run()
    return
  }

  if (!mysqlPool) {
    throw new Error("MySQL pool not initialized. Call initDatabase() first.")
  }

  const runner = conn ?? mysqlPool
  await runner.execute("INSERT IGNORE INTO last_block (id, block) VALUES (1, 0)")
}

function logDbOperation(operation: string, duration: number): void {
  const message = `[DB] ${operation} - ${duration.toFixed(2)}ms`
  console.log(message)

  // Warn if any query takes more than 200ms (warnings are logged to file)
  if (duration > 200) {
    logQueryWarning(operation, duration)
  }
}

export async function initDatabase(
  dbPath: string = "op-geth-sim.db",
  poolSizeOverride?: number,
): Promise<void> {
  if (poolInitialized) {
    return
  }

  if (poolSizeOverride !== undefined) {
    poolSize = poolSizeOverride
  }

  if (dbEngine === "sqlite") {
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
    sqliteWriteDb = new Database(dbPath)

    // Set pragmas for write connection
    sqliteWriteDb.run("PRAGMA journal_mode = WAL") // _journal_mode=WAL
    sqliteWriteDb.run("PRAGMA busy_timeout = 11000") // _busy_timeout=11000 (11 seconds)
    sqliteWriteDb.run("PRAGMA auto_vacuum = incremental") // _auto_vacuum=incremental
    sqliteWriteDb.run("PRAGMA foreign_keys = OFF") // _foreign_keys=true
    sqliteWriteDb.run("PRAGMA cache_size = 100000") // _cache_size=1000000000 (in pages)
    // Note: _txlock=immediate - transactions will use BEGIN IMMEDIATE for immediate locking

    sqliteWriteDb.run(schema)
    await ensureLastBlockInitialized()

    // Initialize read pool (multiple connections for concurrent reads)
    sqliteReadPool = []
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

      sqliteReadPool.push(readDb)
    }

    poolInitialized = true
    console.log(
      `Database initialized (engine=sqlite): ${poolSize} read connections, 1 write connection`,
    )
    return
  }

  // MySQL
  // Configure via MYSQL_URL or discrete env vars.
  const mysqlUrl = process.env.MYSQL_URL
  mysqlPool = mysqlUrl
    ? createPool(mysqlUrl)
    : createPool({
        host: process.env.MYSQL_HOST || "127.0.0.1",
        port: process.env.MYSQL_PORT ? Number(process.env.MYSQL_PORT) : 3306,
        user: process.env.MYSQL_USER || "root",
        password: process.env.MYSQL_PASSWORD || "",
        database: process.env.MYSQL_DATABASE || "op_geth_sim",
        connectionLimit: poolSizeOverride ?? poolSize,
      })

  const pool = mysqlPool

  await pool.execute(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version BIGINT NOT NULL,
      dirty BOOLEAN NOT NULL DEFAULT FALSE,
      PRIMARY KEY (version)
    )
  `)

  await pool.execute(`
    CREATE TABLE IF NOT EXISTS payloads (
      entity_key VARBINARY(255) NOT NULL,
      from_block BIGINT NOT NULL,
      to_block BIGINT NOT NULL,
      payload LONGBLOB NOT NULL,
      content_type VARCHAR(255) NOT NULL,
      string_attributes TEXT NOT NULL,
      numeric_attributes TEXT NOT NULL,
      PRIMARY KEY (entity_key, from_block)
    )
  `)

  await pool.execute(`
    CREATE TABLE IF NOT EXISTS string_attributes (
      entity_key VARBINARY(255) NOT NULL,
      from_block BIGINT NOT NULL,
      to_block BIGINT NOT NULL,
      \`key\` VARCHAR(191) NOT NULL,
      value TEXT NOT NULL,
      PRIMARY KEY (entity_key, \`key\`, from_block)
    )
  `)

  await pool.execute(`
    CREATE TABLE IF NOT EXISTS numeric_attributes (
      entity_key VARBINARY(255) NOT NULL,
      from_block BIGINT NOT NULL,
      to_block BIGINT NOT NULL,
      \`key\` VARCHAR(191) NOT NULL,
      value BIGINT NOT NULL,
      PRIMARY KEY (entity_key, \`key\`, from_block)
    )
  `)

  await pool.execute(`
    CREATE TABLE IF NOT EXISTS last_block (
      id TINYINT NOT NULL,
      block BIGINT NOT NULL,
      PRIMARY KEY (id)
    )
  `)

  await pool.execute(`
    CREATE TABLE IF NOT EXISTS entity_receipts (
      id VARCHAR(255) NOT NULL PRIMARY KEY,
      entity_key TEXT NOT NULL,
      created_at_block BIGINT NOT NULL
    )
  `)

  // Indexes mirrored from arkiv.schema.sql (SQLite).
  // Note: MySQL cannot index full TEXT; we use a prefix length for `value`.
  await pool.execute(
    "CREATE INDEX IF NOT EXISTS string_attributes_entity_key_value_index ON string_attributes (from_block, to_block, `key`, value(191))",
  )
  await pool.execute(
    "CREATE INDEX IF NOT EXISTS string_attributes_kv_temporal_idx ON string_attributes (`key`, value(191), from_block DESC, to_block DESC)",
  )
  await pool.execute(
    "CREATE INDEX IF NOT EXISTS string_attributes_entity_key_index ON string_attributes (from_block, to_block, `key`)",
  )
  await pool.execute("CREATE INDEX IF NOT EXISTS string_attributes_delete_index ON string_attributes (to_block)")
  await pool.execute(
    "CREATE INDEX IF NOT EXISTS string_attributes_entity_kv_idx ON string_attributes (entity_key, `key`, from_block DESC)",
  )

  await pool.execute(
    "CREATE INDEX IF NOT EXISTS numeric_attributes_entity_key_value_index ON numeric_attributes (from_block, to_block, `key`, value)",
  )
  await pool.execute(
    "CREATE INDEX IF NOT EXISTS numeric_attributes_entity_key_index ON numeric_attributes (from_block, to_block, `key`)",
  )
  await pool.execute(
    "CREATE INDEX IF NOT EXISTS numeric_attributes_kv_temporal_idx ON numeric_attributes (`key`, value, from_block DESC, to_block DESC)",
  )
  await pool.execute("CREATE INDEX IF NOT EXISTS numeric_attributes_delete_index ON numeric_attributes (to_block)")

  await pool.execute(
    "CREATE INDEX IF NOT EXISTS payloads_entity_key_index ON payloads (entity_key, from_block, to_block)",
  )
  await pool.execute("CREATE INDEX IF NOT EXISTS payloads_delete_index ON payloads (to_block)")

  await ensureLastBlockInitialized()

  poolInitialized = true
  console.log(`Database initialized (engine=mysql): pool size ${poolSize}`)
}

/**
 * Get a read connection from the pool (round-robin)
 */
function getSqliteReadConnection(): Database {
  if (!poolInitialized || sqliteReadPool.length === 0) {
    throw new Error("SQLite pool not initialized. Call initDatabase() first.")
  }

  // Round-robin selection
  const connection = sqliteReadPool[currentReadIndex]
  currentReadIndex = (currentReadIndex + 1) % sqliteReadPool.length
  return connection
}

/**
 * Get the write connection (single connection for all writes)
 */
function getSqliteWriteConnection(): Database {
  if (!sqliteWriteDb) {
    throw new Error("SQLite DB not initialized. Call initDatabase() first.")
  }
  return sqliteWriteDb
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
  if (dbEngine !== "sqlite") {
    throw new Error("getDatabase() is only available for SQLite engine")
  }
  return getSqliteReadConnection()
}

export async function closeDatabase(): Promise<void> {
  if (dbEngine === "sqlite") {
    // Close all read connections
    for (const db of sqliteReadPool) {
      try {
        db.close()
      } catch (error) {
        console.error("Error closing read connection:", error)
      }
    }
    sqliteReadPool = []

    // Close write connection
    if (sqliteWriteDb) {
      try {
        sqliteWriteDb.close()
      } catch (error) {
        console.error("Error closing write connection:", error)
      }
      sqliteWriteDb = null
    }
  } else {
    if (mysqlPool) {
      await mysqlPool.end()
      mysqlPool = null
    }
  }

  poolInitialized = false
  currentReadIndex = 0
}

type QueryParam = string | number | Buffer | null

async function dbAll(sql: string, params: QueryParam[] = [], conn?: PoolConnection): Promise<any[]> {
  if (dbEngine === "sqlite") {
    const database = getSqliteReadConnection()
    const stmt = database.prepare(sql)
    return (params.length > 0 ? stmt.all(...params) : stmt.all()) as any[]
  }

  if (!mysqlPool) {
    throw new Error("MySQL pool not initialized. Call initDatabase() first.")
  }

  const runner = conn ?? mysqlPool
  const [rows] = await runner.execute(sql, params)
  return rows as any[]
}

async function dbGet(sql: string, params: QueryParam[] = [], conn?: PoolConnection): Promise<any | undefined> {
  if (dbEngine === "sqlite") {
    const database = getSqliteReadConnection()
    const stmt = database.prepare(sql)
    return (params.length > 0 ? stmt.get(...params) : stmt.get()) as any | undefined
  }

  const rows = await dbAll(sql, params, conn)
  return rows[0]
}

async function dbRun(sql: string, params: QueryParam[] = [], conn?: PoolConnection): Promise<void> {
  if (dbEngine === "sqlite") {
    const database = getSqliteWriteConnection()
    const stmt = database.prepare(sql)
    if (params.length > 0) stmt.run(...params)
    else stmt.run()
    return
  }

  if (!mysqlPool) {
    throw new Error("MySQL pool not initialized. Call initDatabase() first.")
  }

  const runner = conn ?? mysqlPool
  await runner.execute(sql, params)
}

async function withTransaction<T>(fn: (conn?: PoolConnection) => Promise<T>): Promise<T> {
  if (dbEngine === "sqlite") {
    const database = getSqliteWriteConnection()
    database.run("BEGIN IMMEDIATE TRANSACTION")
    try {
      const result = await fn(undefined)
      database.run("COMMIT")
      return result
    } catch (error) {
      database.run("ROLLBACK")
      throw error
    }
  }

  if (!mysqlPool) {
    throw new Error("MySQL pool not initialized. Call initDatabase() first.")
  }

  const conn = await mysqlPool.getConnection()
  try {
    await conn.beginTransaction()
    const result = await fn(conn)
    await conn.commit()
    return result
  } catch (error) {
    try {
      await conn.rollback()
    } finally {
      conn.release()
    }
    throw error
  } finally {
    conn.release()
  }
}

async function insertEntityInternal(entity: Entity, conn?: PoolConnection): Promise<void> {
  const startTime = performance.now()

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
    Object.keys(stringAnnotations).length > 0 ? JSON.stringify(stringAnnotations) : "{}"
  const numericAttributesJson = entity.numericAnnotations
    ? JSON.stringify(entity.numericAnnotations)
    : "{}"

  // Insert into payloads table
  // from_block uses lastModifiedAtBlock, to_block uses expiresAt
  await dbRun(
    `
      INSERT INTO payloads (
        entity_key, from_block, to_block, payload, content_type,
        string_attributes, numeric_attributes
      ) VALUES (?, ?, ?, ?, ?, ?, ?)
    `,
    [
      entityKeyBuffer,
      entity.lastModifiedAtBlock,
      entity.expiresAt,
      payload,
      entity.contentType,
      stringAttributesJson,
      numericAttributesJson,
    ],
    conn,
  )

  // Insert string attributes into separate table for querying
  if (Object.keys(stringAnnotations).length > 0) {
    for (const [key, value] of Object.entries(stringAnnotations)) {
      await dbRun(
        `
          INSERT INTO string_attributes (
            entity_key, from_block, to_block, \`key\`, value
          ) VALUES (?, ?, ?, ?, ?)
        `,
        [entityKeyBuffer, entity.lastModifiedAtBlock, entity.expiresAt, key, value],
        conn,
      )
    }
  }

  // Insert numeric attributes into separate table for querying
  if (entity.numericAnnotations) {
    for (const [key, value] of Object.entries(entity.numericAnnotations)) {
      await dbRun(
        `
          INSERT INTO numeric_attributes (
            entity_key, from_block, to_block, \`key\`, value
          ) VALUES (?, ?, ?, ?, ?)
        `,
        [entityKeyBuffer, entity.lastModifiedAtBlock, entity.expiresAt, key, value],
        conn,
      )
    }
  }

  const duration = performance.now() - startTime
  logDbOperation(`insertEntity(key=${entity.key})`, duration)
}

export async function insertEntity(entity: Entity): Promise<void> {
  await insertEntityInternal(entity)
}

async function updateBlockNumberInternal(blockNumber: number, conn?: PoolConnection): Promise<void> {
  if (dbEngine === "sqlite") {
    await dbRun(
      "INSERT INTO last_block (id, block) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET block = excluded.block",
      [blockNumber],
      conn,
    )
    return
  }

  await dbRun(
    "INSERT INTO last_block (id, block) VALUES (1, ?) ON DUPLICATE KEY UPDATE block = VALUES(block)",
    [blockNumber],
    conn,
  )
}

export async function insertEntitiesBatch(
  entities: PendingEntity[],
  blockNumber: number = 0,
): Promise<void> {
  const startTime = performance.now()
  if (dbEngine === "mysql") {
    await withTransaction(async (conn) => {
      if (!conn) {
        throw new Error("MySQL transaction connection is missing")
      }

      const payloadRows: QueryParam[][] = []
      const receiptRows: QueryParam[][] = []
      const stringAttrRows: QueryParam[][] = []
      const numericAttrRows: QueryParam[][] = []

      for (const entity of entities) {
        entity.createdAtBlock = blockNumber
        entity.lastModifiedAtBlock = blockNumber

        const entityKeyBuffer = Buffer.from(entity.key, "utf-8")
        const payload =
          typeof entity.payload === "string"
            ? Buffer.from(entity.payload, "base64")
            : entity.payload || null

        const stringAnnotations = entity.stringAnnotations ? { ...entity.stringAnnotations } : {}
        if (entity.ownerAddress) {
          stringAnnotations.ownerAddress = entity.ownerAddress
        }

        const stringAttributesJson =
          Object.keys(stringAnnotations).length > 0 ? JSON.stringify(stringAnnotations) : "{}"
        const numericAttributesJson = entity.numericAnnotations
          ? JSON.stringify(entity.numericAnnotations)
          : "{}"

        payloadRows.push([
          entityKeyBuffer,
          entity.lastModifiedAtBlock,
          entity.expiresAt,
          payload,
          entity.contentType,
          stringAttributesJson,
          numericAttributesJson,
        ])

        receiptRows.push([entity.id, entity.key, entity.createdAtBlock])

        for (const [key, value] of Object.entries(stringAnnotations)) {
          stringAttrRows.push([
            entityKeyBuffer,
            entity.lastModifiedAtBlock,
            entity.expiresAt,
            key,
            value,
          ])
        }

        if (entity.numericAnnotations) {
          for (const [key, value] of Object.entries(entity.numericAnnotations)) {
            numericAttrRows.push([
              entityKeyBuffer,
              entity.lastModifiedAtBlock,
              entity.expiresAt,
              key,
              value,
            ])
          }
        }
      }

      async function bulkInsert(
        table: string,
        columns: string[],
        rows: QueryParam[][],
        chunkRows: number,
      ): Promise<void> {
        if (rows.length === 0) return

        const rowWidth = columns.length
        const rowPlaceholders = `(${Array.from({ length: rowWidth }, () => "?").join(", ")})`

        for (let i = 0; i < rows.length; i += chunkRows) {
          const chunk = rows.slice(i, i + chunkRows)
          const sql = `INSERT INTO ${table} (${columns.join(", ")}) VALUES ${chunk
            .map(() => rowPlaceholders)
            .join(", ")}`
          const params = chunk.flat()
          await dbRun(sql, params, conn)
        }
      }

      // Conservative chunking for payloads (can include large blobs).
      await bulkInsert(
        "payloads",
        [
          "entity_key",
          "from_block",
          "to_block",
          "payload",
          "content_type",
          "string_attributes",
          "numeric_attributes",
        ],
        payloadRows,
        50,
      )

      await bulkInsert(
        "entity_receipts",
        ["id", "entity_key", "created_at_block"],
        receiptRows,
        1000,
      )

      await bulkInsert(
        "string_attributes",
        ["entity_key", "from_block", "to_block", "`key`", "value"],
        stringAttrRows,
        2000,
      )

      await bulkInsert(
        "numeric_attributes",
        ["entity_key", "from_block", "to_block", "`key`", "value"],
        numericAttrRows,
        2000,
      )

      await updateBlockNumberInternal(blockNumber, conn)
    })

    const duration = performance.now() - startTime
    logDbOperation(`insertEntitiesBatch(count=${entities.length})`, duration)
    return
  }

  await withTransaction(async (conn) => {
    for (const entity of entities) {
      entity.createdAtBlock = blockNumber
      entity.lastModifiedAtBlock = blockNumber
      
      await insertEntityInternal(entity, conn)
      await dbRun(
        `
          INSERT INTO entity_receipts (id, entity_key, created_at_block)
          VALUES (?, ?, ?)
        `,
        [entity.id, entity.key, entity.createdAtBlock],
        conn,
      )
    }
    await updateBlockNumberInternal(blockNumber, conn)
  })
  const duration = performance.now() - startTime
  logDbOperation(`insertEntitiesBatch(count=${entities.length})`, duration)
}

export async function removeExpiredEntities(blockNumber: number): Promise<void> {
  const startTime = performance.now()
  await withTransaction(async (conn) => {
    await dbRun("DELETE FROM payloads WHERE to_block = ?", [blockNumber], conn)
  })
  const duration = performance.now() - startTime
  logDbOperation(`removeExpiredEntities(blockNumber=${blockNumber})`, duration)
}

export async function getEntityByKey(key: string): Promise<Entity | null> {
  const startTime = performance.now()

  // Convert key to BLOB for querying
  const entityKeyBuffer = Buffer.from(key, "utf-8")

  const row = (await dbGet(
    `
      SELECT * FROM payloads
      WHERE entity_key = ?
      ORDER BY from_block DESC
      LIMIT 1
    `,
    [entityKeyBuffer],
  )) as any
  if (!row) {
    const duration = performance.now() - startTime
    logDbOperation(`getEntityByKey(key=${key}) - not found`, duration)
    // Log query details even when entity is not found
    logQuery("getEntityByKey", duration, { key, found: false })
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

  // Log query details to query.log
  logQuery("getEntityByKey", duration, { key, found: true })

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

/**
 * Build Arkiv query language string from filter parameters.
 * Reference: https://github.com/Arkiv-Network/arkiv-sdk-python?tab=readme-ov-file#query-language
 * 
 * Supports range queries for numeric annotations:
 * - number: exact match (e.g., 8 -> "cpu_count = 8")
 * - string with operator: range query (e.g., ">=8" -> "cpu_count >= 8")
 *   Supported operators: >=, <=, >, <, !=
 */
function buildArkivQuery(
  ownerAddress?: string,
  stringAnnotations?: Record<string, string>,
  numericAnnotations?: Record<string, number | string>,
): string {
  const conditions: string[] = []

  // Filter by owner_address if provided
  if (ownerAddress) {
    conditions.push(`ownerAddress = "${ownerAddress}"`)
  }

  // Filter by string annotations (equality)
  if (stringAnnotations && Object.keys(stringAnnotations).length > 0) {
    for (const [key, value] of Object.entries(stringAnnotations)) {
      // Escape double quotes in string values
      const escapedValue = value.replace(/"/g, '\\"')
      conditions.push(`${key} = "${escapedValue}"`)
    }
  }

  // Filter by numeric annotations (equality or range)
  // Supports: number (exact match) or string with operator (>=, <=, >, <, !=)
  if (numericAnnotations && Object.keys(numericAnnotations).length > 0) {
    for (const [key, value] of Object.entries(numericAnnotations)) {
      if (typeof value === "number") {
        // Exact match
        conditions.push(`${key} = ${value}`)
      } else if (typeof value === "string") {
        // Range query with operator
        // Parse format: ">=8", "<=32", ">16", "<64", "!=0"
        const rangeMatch = value.match(/^(>=|<=|>|<|!=)\s*(\d+(?:\.\d+)?)$/)
        if (rangeMatch) {
          const operator = rangeMatch[1]
          const numValue = rangeMatch[2]
          conditions.push(`${key} ${operator} ${numValue}`)
        } else {
          // Fallback: try to parse as number for backward compatibility
          const numValue = parseFloat(value)
          if (!Number.isNaN(numValue)) {
            conditions.push(`${key} = ${numValue}`)
          }
        }
      }
    }
  }

  // Join all conditions with AND
  return conditions.join(" AND ")
}

// Query cache: stores SQL query and parameters for reuse
interface CachedQuery {
  sqlQuery: string
  params: (string | number)[]
}

const queryCache = new Map<string, CachedQuery>()
let queryCacheEnabled = false // Cache is disabled by default

/**
 * Generate normalized cache key from Arkiv query structure (ignoring values).
 * Only includes attribute keys, operators, limit, and offset - not the actual values.
 */
function getCacheKey(arkivQuery: string, limit: number, offset: number): string {
  if (!arkivQuery || arkivQuery.trim() === "") {
    return `empty|limit:${limit}|offset:${offset}`
  }

  // Parse query to extract structure (keys and operators) without values
  const conditions = parseArkivQuery(arkivQuery)
  
  // Build normalized key: only include attribute keys and operators
  const normalizedParts: string[] = []
  
  for (const cond of conditions) {
    const type = cond.isNumeric ? "num" : "str"
    normalizedParts.push(`${type}:${cond.key}:${cond.operator}`)
  }
  
  // Sort to ensure consistent cache keys regardless of order
  normalizedParts.sort()
  
  return `${normalizedParts.join(",")}|limit:${limit}|offset:${offset}`
}

/**
 * Parse Arkiv query string into structured conditions
 */
interface ParsedCondition {
  key: string
  operator: string
  value: string | number
  isNumeric: boolean
}

function parseArkivQuery(arkivQuery: string): ParsedCondition[] {
  if (!arkivQuery || arkivQuery.trim() === "") {
    return []
  }

  const conditions: ParsedCondition[] = []
  // Split by AND (simple parsing - doesn't handle parentheses yet)
  const andConditions = arkivQuery.split(" AND ").map((c) => c.trim())

  for (const condition of andConditions) {
    // Parse equality: key = "value" or key = number
    const equalityMatch = condition.match(/^(\w+)\s*=\s*(.+)$/)
    if (equalityMatch) {
      const key = equalityMatch[1]
      let value = equalityMatch[2].trim()

      // Remove quotes from string values
      if (value.startsWith('"') && value.endsWith('"')) {
        value = value.slice(1, -1).replace(/\\"/g, '"')
        conditions.push({
          key,
          operator: "=",
          value,
          isNumeric: false,
        })
      } else {
        // Numeric annotation (equality)
        const numValue = parseFloat(value)
        if (!Number.isNaN(numValue)) {
          conditions.push({
            key,
            operator: "=",
            value: numValue,
            isNumeric: true,
          })
        }
      }
    } else {
      // Parse range operators: key >= number, key <= number, key > number, key < number, key != number
      const rangeMatch = condition.match(/^(\w+)\s*(>=|<=|>|<|!=)\s*(\d+(?:\.\d+)?)$/)
      if (rangeMatch) {
        const key = rangeMatch[1]
        const operator = rangeMatch[2]
        const numValue = parseFloat(rangeMatch[3])

        if (!Number.isNaN(numValue)) {
          conditions.push({
            key,
            operator,
            value: numValue,
            isNumeric: true,
          })
        }
      }
    }
  }

  return conditions
}


/**
 * Convert Arkiv query string to SQL query using CTEs with INTERSECT.
 * 
 * Pattern:
 * - Each attribute condition gets its own CTE (table_1, table_2, table_4, etc.)
 * - After each pair, create an INTERSECT CTE (table_3 = table_1 INTERSECT table_2)
 * - Final table is DISTINCT
 * - Final SELECT joins the intersected keys with payloads
 */
function buildSqlFromArkivQuery(
  arkivQuery: string,
  currentBlock: number,
  limit: number,
  offset: number,
): { sqlQuery: string; params: (string | number)[] } {
  const conditions = parseArkivQuery(arkivQuery)
  
  // If no conditions, return simple query
  if (conditions.length === 0) {
    const sqlQuery = `SELECT e.content_type AS content_type, e.entity_key AS entity_key, expirationAttrs.Value AS expires_at, e.from_block AS from_block, e.numeric_attributes AS numeric_attributes, ownerAttrs.Value AS owner, e.payload AS payload, e.string_attributes AS string_attributes FROM payloads AS e LEFT JOIN string_attributes AS ownerAttrs ON e.entity_key = ownerAttrs.entity_key AND e.from_block = ownerAttrs.from_block AND ownerAttrs.key = '$owner' LEFT JOIN numeric_attributes AS expirationAttrs ON e.entity_key = expirationAttrs.entity_key AND e.from_block = expirationAttrs.from_block AND expirationAttrs.key = '$expiration' WHERE ? BETWEEN e.from_block AND e.to_block - 1 ORDER BY from_block, entity_key LIMIT ${limit}${offset > 0 ? ` OFFSET ${offset}` : ""}`
    return { sqlQuery, params: [currentBlock] }
  }

  const ctes: string[] = []
  const params: (string | number)[] = []

  // Step 1: Build CTEs for each condition (table_1, table_2, table_4, table_6, table_8, table_10, ...)
  // Note: table numbers skip for intersect tables, but we'll number them sequentially first
  for (let i = 0; i < conditions.length; i++) {
    const cond = conditions[i]
    const tableNum = i + 1
    const tableName = `table_${tableNum}`
    const attrTable = cond.isNumeric ? "numeric_attributes" : "string_attributes"
    const valueOperator = cond.isNumeric ? cond.operator : "="
    
    // Build CTE: SELECT entity_key, from_block from attributes joined with payloads
    // Use ? for parameters: currentBlock, key, value (matching SQL order)
    const cte = `${tableName} AS (SELECT e.entity_key, e.from_block FROM ${attrTable} AS a INNER JOIN payloads AS e ON a.entity_key = e.entity_key AND a.from_block = e.from_block AND ? BETWEEN e.from_block AND e.to_block - 1 WHERE key = ? AND value ${valueOperator} ?)`
    
    ctes.push(cte)
    
    // Parameters: currentBlock, key, value (matching SQL placeholder order)
    params.push(currentBlock)
    params.push(cond.key)
    params.push(cond.value as string | number)
  }

  // Step 2: Build INTERSECT chain
  // For N conditions, we need N-1 intersections
  // Pattern: table_3 = table_1 INTERSECT table_2, table_5 = table_3 INTERSECT table_4, etc.
  let nextIntersectTableNum = conditions.length + 1
  let lastTable = "table_1"
  
  for (let i = 1; i < conditions.length; i++) {
    const currentTable = `table_${i + 1}`
    const intersectTable = `table_${nextIntersectTableNum}`
    const isLast = i === conditions.length - 1
    
    if (isLast) {
      // Last intersection - make it DISTINCT
      ctes.push(`${intersectTable} AS (SELECT DISTINCT * FROM ${lastTable} INTERSECT SELECT * FROM ${currentTable})`)
    } else {
      ctes.push(`${intersectTable} AS (SELECT * FROM ${lastTable} INTERSECT SELECT * FROM ${currentTable})`)
    }
    
    lastTable = intersectTable
    nextIntersectTableNum++
  }

  // Step 3: Build final SELECT
  const finalTable = conditions.length === 1 ? "table_1" : `table_${nextIntersectTableNum - 1}`
  const finalSelect = `SELECT e.content_type AS content_type, e.entity_key AS entity_key, expirationAttrs.Value AS expires_at, e.from_block AS from_block, e.numeric_attributes AS numeric_attributes, ownerAttrs.Value AS owner, e.payload AS payload, e.string_attributes AS string_attributes FROM ${finalTable} AS keys INNER JOIN payloads AS e ON keys.entity_key = e.entity_key AND keys.from_block = e.from_block INNER JOIN string_attributes AS ownerAttrs ON e.entity_key = ownerAttrs.entity_key AND e.from_block = ownerAttrs.from_block AND ownerAttrs.key = '$owner' INNER JOIN numeric_attributes AS expirationAttrs ON e.entity_key = expirationAttrs.entity_key AND e.from_block = expirationAttrs.from_block AND expirationAttrs.key = '$expiration' WHERE ? BETWEEN e.from_block AND e.to_block ORDER BY from_block, entity_key LIMIT ${limit}${offset > 0 ? ` OFFSET ${offset}` : ""}`
  
  params.push(currentBlock)
  
  const sqlQuery = `WITH ${ctes.join(", ")} ${finalSelect}`
  return { sqlQuery, params }
}

/**
 * Execute Arkiv query against SQLite database.
 * Converts Arkiv query string to SQL and executes it.
 * 
 * Optionally caches SQL query structure (with placeholders) to avoid repeated parsing.
 * Parameters are built fresh each time from the actual query values.
 */
async function executeArkivQuery(
  arkivQuery: string,
  limit: number,
  offset: number,
): Promise<Array<Record<string, unknown>>> {
  // Get current block number (needed for building params)
  const currentBlock = await getCurrentBlockNumber()

  let sqlQuery: string
  let params: (string | number)[]

  // Check cache first if enabled - cache key ignores actual values
  if (queryCacheEnabled) {
    const cacheKey = getCacheKey(arkivQuery, limit, offset)
    const cached = queryCache.get(cacheKey)
    
    if (cached) {
      // Reuse cached SQL query structure (with placeholders)
      console.log("cached.sqlQuery hit!!!", cached.sqlQuery)
      sqlQuery = cached.sqlQuery
      params = cached.params
    } else {
      // Build SQL query from Arkiv query (first time for this structure)
      const result = buildSqlFromArkivQuery(arkivQuery, currentBlock, limit, offset)
      sqlQuery = result.sqlQuery
      params = result.params

      queryCache.set(cacheKey, {
        sqlQuery,
        params,
      })
    }
  } else {
    // Cache disabled - always build SQL query from scratch
    const result = buildSqlFromArkivQuery(arkivQuery, currentBlock, limit, offset)
    sqlQuery = result.sqlQuery
    params = result.params
  }

  // Execute the SQL query with fresh parameters
  console.log("sqlQuery", sqlQuery)
  console.log("params", params)
  const rows = (await dbAll(sqlQuery, params as QueryParam[])) as Array<Record<string, unknown>>
  return rows
}

export async function queryEntities(
  ownerAddress?: string,
  stringAnnotations?: Record<string, string>,
  numericAnnotations?: Record<string, number | string>,
  limit: number = 100,
  offset: number = 0,
  withAnnotations: boolean = false,
): Promise<Entity[]> {
  const startTime = performance.now()

  // Build Arkiv query string
  const arkivQuery = buildArkivQuery(ownerAddress, stringAnnotations, numericAnnotations)
  console.log("arkivQuery", arkivQuery)

  // Execute query and get rows (now async)
  const rows = await executeArkivQuery(arkivQuery, limit, offset)

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
          const strAttrs = typeof row.string_attributes === "string" 
            ? row.string_attributes 
            : String(row.string_attributes)
          stringAnnotations = JSON.parse(strAttrs) as Record<string, string>
        } catch (e) {
          console.warn(`Failed to parse string_attributes:`, e)
        }
      }
    }

    if (withAnnotations || row.numeric_attributes) {
      if (row.numeric_attributes) {
        try {
          const numAttrs = typeof row.numeric_attributes === "string"
            ? row.numeric_attributes
            : String(row.numeric_attributes)
          numericAnnotations = JSON.parse(numAttrs) as Record<string, number>
        } catch (e) {
          console.warn(`Failed to parse numeric_attributes:`, e)
        }
      }
    }

    // Convert entity_key BLOB to string
    // The Go tool returns entity_key, which may be Buffer or string
    const entityKey =
      row.entity_key instanceof Buffer 
        ? `0x${row.entity_key.toString("hex")}` 
        : String(row.entity_key)

    // Get owner_address - the Go tool returns it as 'owner' column or from annotations
    const ownerAddr = (row.owner as string) || stringAnnotations?.ownerAddress || ""

    // Get expires_at - the Go tool returns it as 'expires_at' column
    const expiresAt = (row.expires_at as number) || 0

    // Convert payload - may be Buffer or null
    const payload = row.payload instanceof Buffer 
      ? row.payload 
      : row.payload 
        ? Buffer.from(String(row.payload), "base64")
        : undefined

    return {
      key: entityKey,
      expiresAt: expiresAt,
      payload: payload,
      contentType: (row.content_type as string) || "",
      createdAtBlock: (row.from_block as number) || 0,
      lastModifiedAtBlock: (row.from_block as number) || 0,
      deleted: false,
      transactionIndexInBlock: 0,
      operationIndexInTransaction: 0,
      ownerAddress: ownerAddr,
      stringAnnotations: stringAnnotations,
      numericAnnotations: numericAnnotations,
    }
  })

  // Log query details to query.log
  logQuery(
    "queryEntities",
    duration,
    {
      ownerAddress: ownerAddress || null,
      stringAnnotations: stringAnnotations || null,
      numericAnnotations: numericAnnotations || null,
      limit,
      offset,
      rowCount: rows.length,
    },
  )

  return result
}

export async function getCurrentBlockNumber(): Promise<number> {
  const result = (await dbGet("SELECT block FROM last_block WHERE id = 1")) as
    | { block: number }
    | undefined
  return result?.block ?? 0
}

export async function updateBlockNumber(blockNumber: number): Promise<void> {
  await updateBlockNumberInternal(blockNumber)
}

export async function countEntities(): Promise<number> {
  const startTime = performance.now()
  // Count distinct entity_keys in payloads table
  const result = (await dbGet("SELECT COUNT(DISTINCT entity_key) as count FROM payloads")) as {
    count: number
  }
  const duration = performance.now() - startTime
  logDbOperation(`countEntities(count=${result.count})`, duration)
  return result.count
}

export async function getEntityBasicInfo(
  key: string,
): Promise<{ key: string; createdAtBlock: number } | null> {
  const startTime = performance.now()

  // Convert key to BLOB for querying
  const entityKeyBuffer = Buffer.from(key, "utf-8")

  const row = (await dbGet(
    `
      SELECT entity_key, from_block
      FROM payloads
      WHERE entity_key = ?
      ORDER BY from_block DESC
      LIMIT 1
    `,
    [entityKeyBuffer],
  )) as { entity_key: Buffer; from_block: number } | undefined
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

export async function getReceiptById(id: string): Promise<{
  id: string
  key: string
  createdAtBlock: number
} | null> {
  const startTime = performance.now()
  const row = (await dbGet(
    `
      SELECT id, entity_key, created_at_block
      FROM entity_receipts
      WHERE id = ?
    `,
    [id],
  )) as { id: string; entity_key: string; created_at_block: number } | undefined

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

export async function cleanAllData(): Promise<void> {
  await withTransaction(async (conn) => {
    // Delete all attributes
    await dbRun("DELETE FROM string_attributes", [], conn)
    await dbRun("DELETE FROM numeric_attributes", [], conn)
    // Delete all payloads
    await dbRun("DELETE FROM payloads", [], conn)
    // Delete all receipts
    await dbRun("DELETE FROM entity_receipts", [], conn)
    // Reset last_block
    await dbRun("UPDATE last_block SET block = 0 WHERE id = 1", [], conn)
    await ensureLastBlockInitialized(conn)
  })
  // Clear query cache when cleaning data
  queryCache.clear()
}

/**
 * Clear the query cache.
 * Useful when database schema or data changes significantly.
 */
/**
 * Enable or disable the query cache.
 * @param enabled - Whether to enable query caching (default: false)
 */
export function setQueryCacheEnabled(enabled: boolean): void {
  queryCacheEnabled = enabled
  if (!enabled) {
    // Clear cache when disabling
    queryCache.clear()
  }
}

/**
 * Check if query cache is currently enabled.
 */
export function isQueryCacheEnabled(): boolean {
  return queryCacheEnabled
}

/**
 * Clear the query cache.
 * Useful when database schema or data changes significantly.
 */
export function clearQueryCache(): void {
  queryCache.clear()
}

/**
 * Get the current size of the query cache.
 */
export function getQueryCacheSize(): number {
  return queryCache.size
}

export async function vacuumDatabase(): Promise<void> {
  if (dbEngine === "mysql") {
    // MySQL doesn't have VACUUM; OPTIMIZE can be expensive and require privileges.
    return
  }

  const database = getSqliteWriteConnection()

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
