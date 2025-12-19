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
 * Generate unique alias for attribute join
 */
function generateAttributeAlias(seed: number): string {
  // Generate a unique alias similar to Go tool: arkiv_attr_<number>
  return `arkiv_attr_${Math.abs(seed)}`
}

/**
 * Convert Arkiv query string to SQL query.
 * 
 * Pattern:
 * - Each string/numeric annotation gets an INNER JOIN
 * - LEFT JOINs for owner and expiration attributes
 * - WHERE clause filters by current block and attribute values
 */
function buildSqlFromArkivQuery(
  arkivQuery: string,
  currentBlock: number,
  limit: number,
  offset: number,
): { sqlQuery: string; params: (string | number)[] } {
  const conditions = parseArkivQuery(arkivQuery)
  
  // Separate string and numeric conditions
  const stringConditions: ParsedCondition[] = []
  const numericConditions: ParsedCondition[] = []
  
  for (const cond of conditions) {
    if (cond.isNumeric) {
      numericConditions.push(cond)
    } else {
      stringConditions.push(cond)
    }
  }

  // Build SELECT clause
  const selectClause = `SELECT e.content_type AS content_type, e.entity_key AS entity_key, expirationAttrs.Value AS expires_at, e.from_block AS from_block, e.numeric_attributes AS numeric_attributes, ownerAttrs.Value AS owner, e.payload AS payload, e.string_attributes AS string_attributes`

  // Build FROM clause
  const fromClause = `FROM payloads AS e`

  // Build INNER JOINs for string attributes
  const stringJoins: string[] = []
  const stringJoinAliases: string[] = []
  let aliasSeed = 0

  for (let i = 0; i < stringConditions.length; i++) {
    const alias = generateAttributeAlias(aliasSeed++)
    stringJoinAliases.push(alias)
    stringJoins.push(
      `INNER JOIN string_attributes AS ${alias} ON e.entity_key = ${alias}.entity_key AND e.from_block = ${alias}.from_block AND ${alias}.key = ?`
    )
  }

  // Build INNER JOINs for numeric attributes
  const numericJoins: string[] = []
  const numericJoinAliases: string[] = []

  for (let i = 0; i < numericConditions.length; i++) {
    const alias = generateAttributeAlias(aliasSeed++)
    numericJoinAliases.push(alias)
    numericJoins.push(
      `INNER JOIN numeric_attributes AS ${alias} ON e.entity_key = ${alias}.entity_key AND e.from_block = ${alias}.from_block AND ${alias}.key = ?`
    )
  }

  // Build LEFT JOINs for owner and expiration
  const leftJoins = [
    `LEFT JOIN string_attributes AS ownerAttrs ON e.entity_key = ownerAttrs.entity_key AND e.from_block = ownerAttrs.from_block AND ownerAttrs.key = '$owner'`,
    `LEFT JOIN numeric_attributes AS expirationAttrs ON e.entity_key = expirationAttrs.entity_key AND e.from_block = expirationAttrs.from_block AND expirationAttrs.key = '$expiration'`,
  ]

  // Build WHERE clause
  const whereConditions: string[] = []
  whereConditions.push(`? BETWEEN e.from_block AND e.to_block - 1`)

  // Add attribute value filters
  const valueFilters: string[] = []
  
  for (let i = 0; i < stringConditions.length; i++) {
    const alias = stringJoinAliases[i]
    valueFilters.push(`${alias}.value = ?`)
  }

  for (let i = 0; i < numericConditions.length; i++) {
    const alias = numericJoinAliases[i]
    const numericCond = numericConditions[i]
    valueFilters.push(`${alias}.value ${numericCond.operator} ?`)
  }

  if (valueFilters.length > 0) {
    whereConditions.push(`(${valueFilters.join(" AND ")})`)
  }

  // Build ORDER BY and LIMIT
  const orderByClause = `ORDER BY from_block, entity_key`
  const limitClause = `LIMIT ${limit}`
  const offsetClause = offset > 0 ? `OFFSET ${offset}` : ""

  // Combine all parts
  const allJoins = [...stringJoins, ...numericJoins, ...leftJoins]
  const sqlQuery = [
    selectClause,
    fromClause,
    ...allJoins,
    `WHERE ${whereConditions.join(" AND ")}`,
    orderByClause,
    limitClause,
    offsetClause,
  ]
    .filter((part) => part !== "")
    .join(" ")

  // Build parameters array
  // Order: attribute keys (string then numeric), current_block, attribute values (string then numeric)
  const params: (string | number)[] = []
  
  // Attribute keys (string attributes first)
  for (const cond of stringConditions) {
    params.push(cond.key)
  }
  
  // Attribute keys (numeric attributes)
  for (const cond of numericConditions) {
    params.push(cond.key)
  }

  // Current block parameter
  params.push(currentBlock)
  
  // Attribute values (string attributes)
  for (const cond of stringConditions) {
    params.push(cond.value as string)
  }
  
  // Attribute values (numeric attributes)
  for (const cond of numericConditions) {
    params.push(cond.value as number)
  }

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
  database: Database,
  arkivQuery: string,
  limit: number,
  offset: number,
): Promise<Array<Record<string, unknown>>> {
  // Get current block number (needed for building params)
  const currentBlock = getCurrentBlockNumber()

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
      
      // Build fresh parameters from the actual query values
      // The SQL structure is the same, but values may differ
      const { params: freshParams } = buildSqlFromArkivQuery(arkivQuery, currentBlock, limit, offset)
      params = freshParams
    } else {
      // Build SQL query from Arkiv query (first time for this structure)
      const result = buildSqlFromArkivQuery(arkivQuery, currentBlock, limit, offset)
      sqlQuery = result.sqlQuery
      params = result.params

      // Cache only the SQL query structure (with placeholders)
      // Don't cache parameters since they change with each query
      queryCache.set(cacheKey, {
        sqlQuery,
        params: [], // Empty - params are built fresh each time
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
  const stmt = database.prepare(sqlQuery)
  const rows = stmt.all(...params) as Array<Record<string, unknown>>

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
  const database = getReadConnection()

  // Build Arkiv query string
  const arkivQuery = buildArkivQuery(ownerAddress, stringAnnotations, numericAnnotations)
  console.log("arkivQuery", arkivQuery)

  // Execute query and get rows (now async)
  const rows = await executeArkivQuery(database, arkivQuery, limit, offset)

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
