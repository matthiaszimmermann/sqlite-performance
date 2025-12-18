#!/usr/bin/env node

import { Database } from "bun:sqlite"
import { randomBytes } from "crypto"
import { appendFileSync, statSync, writeFileSync } from "fs"
import { performance } from "perf_hooks"

// Configuration
const SOURCE_DB = "mendoza.db"
const TARGET_DB = "output.db"
const BLOCK_POOL_SIZE = 1000 // Number of blocks to keep in memory
const BATCH_SIZE = 100 // Number of blocks to write in each batch
const CSV_LOG_FILE = "replication_log.csv"

interface BlockData {
  payloads: Array<{
    entity_key: Buffer
    from_block: number
    to_block: number
    payload: Buffer | null
    content_type: string | null
    string_attributes: string | null
    numeric_attributes: string | null
  }>
  stringAttributes: Array<{
    entity_key: Buffer
    from_block: number
    to_block: number
    key: string
    value: string
  }>
  numericAttributes: Array<{
    entity_key: Buffer
    from_block: number
    to_block: number
    key: string
    value: number
  }>
}

let blockPool: BlockData[] = []
let targetDb: Database | null = null
let totalBlocksReplicated = 0
let totalPayloads = 0
let totalStringAttrs = 0
let totalNumericAttrs = 0
const writeTimes: number[] = []
const totalBlockTimes: number[] = []

function generateNewEntityKey(): Buffer {
  return randomBytes(32) // Generate 32-byte entity key
}

function getAvailableBlocks(sourceDb: Database): number[] {
  const stmt = sourceDb.prepare(`
    SELECT from_block 
    FROM payloads 
    GROUP BY from_block
    HAVING COUNT(*) < 1500
    ORDER BY from_block
  `)
  const rows = stmt.all() as Array<{ from_block: number }>
  return rows.map((row) => row.from_block)
}

function readBlockData(sourceDb: Database, fromBlock: number): BlockData {
  // Read payloads
  const payloadsStmt = sourceDb.prepare(`
    SELECT entity_key, from_block, to_block, payload, content_type, 
           string_attributes, numeric_attributes
    FROM payloads
    WHERE from_block = ?
  `)
  const payloads = payloadsStmt.all(fromBlock) as BlockData["payloads"]

  // Read string attributes
  const stringAttrsStmt = sourceDb.prepare(`
    SELECT entity_key, from_block, to_block, key, value
    FROM string_attributes
    WHERE from_block = ?
  `)
  const stringAttributes = stringAttrsStmt.all(fromBlock) as BlockData["stringAttributes"]

  // Read numeric attributes
  const numericAttrsStmt = sourceDb.prepare(`
    SELECT entity_key, from_block, to_block, key, value
    FROM numeric_attributes
    WHERE from_block = ?
  `)
  const numericAttributes = numericAttrsStmt.all(fromBlock) as BlockData["numericAttributes"]

  return {
    payloads,
    stringAttributes,
    numericAttributes,
  }
}

function createEntityKeyMap(blockData: BlockData): Map<Buffer, Buffer> {
  const map = new Map<Buffer, Buffer>()
  const seenKeys = new Set<string>()

  // Collect all unique entity keys from the block
  for (const payload of blockData.payloads) {
    const keyStr = payload.entity_key.toString("hex")
    if (!seenKeys.has(keyStr)) {
      seenKeys.add(keyStr)
      map.set(payload.entity_key, generateNewEntityKey())
    }
  }

  for (const attr of blockData.stringAttributes) {
    const keyStr = attr.entity_key.toString("hex")
    if (!seenKeys.has(keyStr)) {
      seenKeys.add(keyStr)
      map.set(attr.entity_key, generateNewEntityKey())
    }
  }

  for (const attr of blockData.numericAttributes) {
    const keyStr = attr.entity_key.toString("hex")
    if (!seenKeys.has(keyStr)) {
      seenKeys.add(keyStr)
      map.set(attr.entity_key, generateNewEntityKey())
    }
  }

  return map
}

function writeReplicatedBlockBatch(targetDb: Database, blocksData: BlockData[]): void {
  const transaction = targetDb.transaction(() => {
    // Prepare statements once for the batch
    const insertPayloadStmt = targetDb.prepare(`
      INSERT INTO payloads 
      (entity_key, from_block, to_block, payload, content_type, string_attributes, numeric_attributes)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `)

    const insertStringAttrStmt = targetDb.prepare(`
      INSERT INTO string_attributes 
      (entity_key, from_block, to_block, key, value)
      VALUES (?, ?, ?, ?, ?)
    `)

    const insertNumericAttrStmt = targetDb.prepare(`
      INSERT INTO numeric_attributes 
      (entity_key, from_block, to_block, key, value)
      VALUES (?, ?, ?, ?, ?)
    `)

    // Process all blocks in the batch
    for (const blockData of blocksData) {
      const entityKeyMap = createEntityKeyMap(blockData)

      // Insert payloads
      for (const payload of blockData.payloads) {
        const newEntityKey = entityKeyMap.get(payload.entity_key) || generateNewEntityKey()
        insertPayloadStmt.run(
          newEntityKey,
          payload.from_block,
          payload.to_block,
          payload.payload,
          payload.content_type,
          payload.string_attributes,
          payload.numeric_attributes,
        )
      }

      // Insert string attributes
      for (const attr of blockData.stringAttributes) {
        const newEntityKey = entityKeyMap.get(attr.entity_key) || generateNewEntityKey()
        insertStringAttrStmt.run(newEntityKey, attr.from_block, attr.to_block, attr.key, attr.value)
      }

      // Insert numeric attributes
      for (const attr of blockData.numericAttributes) {
        const newEntityKey = entityKeyMap.get(attr.entity_key) || generateNewEntityKey()
        insertNumericAttrStmt.run(
          newEntityKey,
          attr.from_block,
          attr.to_block,
          attr.key,
          attr.value,
        )
      }
    }
  })

  transaction()
}

function initializeCsvLog(): void {
  const header =
    "num_payloads,num_string_attributes,num_numeric_attributes,read_time_ms,write_time_ms,output_db_size_bytes\n"
  writeFileSync(CSV_LOG_FILE, header)
}

function writeCsvRow(
  numPayloads: number,
  numStringAttrs: number,
  numNumericAttrs: number,
  readTimeMs: number,
  writeTimeMs: number,
  outputDbSizeBytes: number,
): void {
  const row = `${numPayloads},${numStringAttrs},${numNumericAttrs},${readTimeMs.toFixed(2)},${writeTimeMs.toFixed(2)},${outputDbSizeBytes}\n`
  appendFileSync(CSV_LOG_FILE, row)
}

function getOutputDbSize(): number {
  try {
    const stats = statSync(TARGET_DB)
    return stats.size
  } catch {
    // File might not exist yet, return 0
    return 0
  }
}

function loadBlockPool(sourceDb: Database): void {
  console.log("Loading block pool into memory...")
  const availableBlocks = getAvailableBlocks(sourceDb)
  console.log(`Found ${availableBlocks.length} blocks in source database`)

  if (availableBlocks.length === 0) {
    console.error("No blocks found in source database!")
    sourceDb.close()
    process.exit(1)
  }

  // Randomly select BLOCK_POOL_SIZE blocks (or all if less available)
  const blocksToLoad = Math.min(BLOCK_POOL_SIZE, availableBlocks.length)
  const selectedBlocks: number[] = []
  const availableBlocksCopy = [...availableBlocks]

  // Randomly select blocks
  for (let i = 0; i < blocksToLoad; i++) {
    const randomIndex = Math.floor(Math.random() * availableBlocksCopy.length)
    selectedBlocks.push(availableBlocksCopy[randomIndex])
    availableBlocksCopy.splice(randomIndex, 1)
  }

  console.log(`Loading ${blocksToLoad} blocks into memory...`)
  const loadStartTime = performance.now()

  blockPool = []
  for (const blockNumber of selectedBlocks) {
    const blockData = readBlockData(sourceDb, blockNumber)
    if (
      blockData.payloads.length > 0 ||
      blockData.stringAttributes.length > 0 ||
      blockData.numericAttributes.length > 0
    ) {
      blockPool.push(blockData)
    }
  }

  const loadDuration = performance.now() - loadStartTime
  console.log(
    `Block pool loaded: ${blockPool.length} blocks in memory (${loadDuration.toFixed(2)}ms)`,
  )
}

function initializeTargetDatabase(): void {
  console.log("Opening target database...")
  targetDb = new Database(TARGET_DB)
  targetDb.exec("PRAGMA journal_mode = WAL")
  targetDb.exec("PRAGMA foreign_keys = OFF")
  targetDb.exec("PRAGMA synchronous = NORMAL")
  targetDb.exec("PRAGMA page_size = 4096")
  targetDb.exec("PRAGMA cache_size = -64000") // 64MB cache
  targetDb.exec("PRAGMA wal_autocheckpoint = 1000")

  // Initialize target database schema if needed
  console.log("Initializing target database schema...")
  targetDb.exec(`
    CREATE TABLE IF NOT EXISTS payloads (
      entity_key BLOB,
      from_block INTEGER,
      to_block INTEGER,
      payload BLOB,
      content_type TEXT,
      string_attributes TEXT,
      numeric_attributes TEXT,
      PRIMARY KEY (entity_key, from_block)
    );

    CREATE TABLE IF NOT EXISTS string_attributes (
      entity_key BLOB,
      from_block INTEGER,
      to_block INTEGER,
      key TEXT,
      value TEXT,
      PRIMARY KEY (entity_key, key, from_block)
    );

    CREATE TABLE IF NOT EXISTS numeric_attributes (
      entity_key BLOB,
      from_block INTEGER,
      to_block INTEGER,
      key TEXT,
      value FLOAT,
      PRIMARY KEY (entity_key, key, from_block)
    );

    CREATE TABLE IF NOT EXISTS last_block (
      id INTEGER PRIMARY KEY,
      block INTEGER
    );

    CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY,
      dirty INTEGER
    );
  `)

  // Create indexes
  console.log("Creating indexes...")
  targetDb.exec(`
    CREATE INDEX IF NOT EXISTS payloads_delete_index ON payloads(to_block);
    CREATE INDEX IF NOT EXISTS payloads_entity_key_index ON payloads(entity_key, from_block, to_block);

    CREATE INDEX IF NOT EXISTS string_attributes_delete_index ON string_attributes(to_block);
    CREATE INDEX IF NOT EXISTS string_attributes_entity_key_index ON string_attributes(from_block, to_block, key);
    CREATE INDEX IF NOT EXISTS string_attributes_entity_key_value_index ON string_attributes(from_block, to_block, key, value);
    CREATE INDEX IF NOT EXISTS string_attributes_entity_kv_idx ON string_attributes(entity_key, key, from_block DESC, value, to_block);
    CREATE INDEX IF NOT EXISTS string_attributes_kv_temporal_idx ON string_attributes(key, value, from_block DESC, to_block DESC, entity_key);

    CREATE INDEX IF NOT EXISTS numeric_attributes_delete_index ON numeric_attributes(to_block);
    CREATE INDEX IF NOT EXISTS numeric_attributes_entity_key_index ON numeric_attributes(from_block, to_block, key);
    CREATE INDEX IF NOT EXISTS numeric_attributes_entity_key_value_index ON numeric_attributes(from_block, to_block, key, value);
    CREATE INDEX IF NOT EXISTS numeric_attributes_kv_temporal_idx ON numeric_attributes(key, value, from_block DESC, to_block DESC, entity_key);
  `)
}

function processBatch(batchSize: number = BATCH_SIZE): {
  blocksProcessed: number
  batchPayloads: number
  batchStringAttrs: number
  batchNumericAttrs: number
  writeDuration: number
  batchDuration: number
} {
  if (!targetDb || blockPool.length === 0) {
    throw new Error("Target database or block pool not initialized")
  }

  const batchStartTime = performance.now()

  // Select random blocks from the pool to replicate
  const blocksToReplicate: BlockData[] = []
  for (let i = 0; i < batchSize; i++) {
    const randomIndex = Math.floor(Math.random() * blockPool.length)
    blocksToReplicate.push(blockPool[randomIndex])
  }

  // Calculate totals for logging
  let batchPayloads = 0
  let batchStringAttrs = 0
  let batchNumericAttrs = 0

  for (const blockData of blocksToReplicate) {
    batchPayloads += blockData.payloads.length
    batchStringAttrs += blockData.stringAttributes.length
    batchNumericAttrs += blockData.numericAttributes.length
  }

  const writeStartTime = performance.now()
  writeReplicatedBlockBatch(targetDb, blocksToReplicate)
  const writeDuration = performance.now() - writeStartTime
  writeTimes.push(writeDuration)

  const batchDuration = performance.now() - batchStartTime
  totalBlockTimes.push(batchDuration)

  return {
    blocksProcessed: blocksToReplicate.length,
    batchPayloads,
    batchStringAttrs,
    batchNumericAttrs,
    writeDuration,
    batchDuration,
  }
}

function printFinalStatistics(): void {
  if (totalBlocksReplicated > 0) {
    console.log("\n\n=== Replication Statistics ===")
    console.log(`Total blocks replicated: ${totalBlocksReplicated}`)
    console.log(`Total payloads: ${totalPayloads}`)
    console.log(`Total string attributes: ${totalStringAttrs}`)
    console.log(`Total numeric attributes: ${totalNumericAttrs}`)

    if (writeTimes.length > 0) {
      const avgWriteTime = writeTimes.reduce((a, b) => a + b, 0) / writeTimes.length
      console.log("\n=== Average Times ===")
      console.log(`Write time: ${avgWriteTime.toFixed(2)}ms`)

      const sortedWriteTimes = [...writeTimes].sort((a, b) => a - b)
      const writeP50 = sortedWriteTimes[Math.floor(sortedWriteTimes.length * 0.5)]
      const writeP95 = sortedWriteTimes[Math.floor(sortedWriteTimes.length * 0.95)]
      const writeP99 = sortedWriteTimes[Math.floor(sortedWriteTimes.length * 0.99)]

      console.log("\n=== Write Performance Percentiles ===")
      console.log(`P50 (median): ${writeP50.toFixed(2)}ms`)
      console.log(`P95: ${writeP95.toFixed(2)}ms`)
      console.log(`P99: ${writeP99.toFixed(2)}ms`)
      console.log(`Min: ${Math.min(...writeTimes).toFixed(2)}ms`)
      console.log(`Max: ${Math.max(...writeTimes).toFixed(2)}ms`)
    }
  }
}

async function replicateBlocks(numBlocks: number): Promise<void> {
  console.log("Opening source database (read-only)...")
  const sourceDb = new Database(SOURCE_DB)

  // Load block pool into memory
  loadBlockPool(sourceDb)
  sourceDb.close()

  // Initialize target database
  initializeTargetDatabase()

  // Initialize CSV log file
  console.log(`Initializing CSV log file: ${CSV_LOG_FILE}`)
  initializeCsvLog()

  console.log(
    `Starting block replicator (processing batches of ${BATCH_SIZE} blocks, target: ${numBlocks} blocks)...`,
  )

  const startTime = performance.now()

  try {
    // Continuously process batches until we reach the target number of blocks
    while (totalBlocksReplicated < numBlocks) {
      const remaining = numBlocks - totalBlocksReplicated
      const currentBatchSize = Math.min(BATCH_SIZE, remaining)

      // Process batch with appropriate size
      const result = processBatch(currentBatchSize)

      totalBlocksReplicated += result.blocksProcessed
      totalPayloads += result.batchPayloads
      totalStringAttrs += result.batchStringAttrs
      totalNumericAttrs += result.batchNumericAttrs

      // Write CSV log entry (aggregated for the batch)
      const outputDbSize = getOutputDbSize()
      writeCsvRow(
        result.batchPayloads,
        result.batchStringAttrs,
        result.batchNumericAttrs,
        0, // readTime (blocks are in memory)
        result.writeDuration,
        outputDbSize,
      )

      const message = `[BATCH] Processed ${result.blocksProcessed} blocks: ${result.batchPayloads} payloads, ${result.batchStringAttrs} str attrs, ${result.batchNumericAttrs} num attrs - ${result.batchDuration.toFixed(2)}ms`
      console.log(message)

      // Warn if batch processing takes more than 1000ms
      if (result.batchDuration > 1000) {
        console.warn(`⚠️  WARNING: Batch processing took ${result.batchDuration.toFixed(2)}ms`)
      }
    }
  } catch (error) {
    console.error("\nError during replication:", error)
    throw error
  } finally {
    const totalTime = (performance.now() - startTime) / 1000
    console.log(`\nTotal time: ${totalTime.toFixed(2)}s`)
    console.log(`Blocks per second: ${(totalBlocksReplicated / totalTime).toFixed(2)}`)

    printFinalStatistics()

    if (targetDb) {
      targetDb.close()
      targetDb = null
    }
  }
}

// Main execution
async function main(): Promise<void> {
  const args = process.argv.slice(2)
  const numBlocks = args[0] ? parseInt(args[0], 10) : Infinity

  if (args[0] && (Number.isNaN(numBlocks) || numBlocks <= 0)) {
    console.error("Error: Number of blocks must be a positive number")
    console.error("Usage: tsx src/block-replicator-fast.ts [num_blocks]")
    console.error("Example: tsx src/block-replicator-fast.ts 1000")
    process.exit(1)
  }

  await replicateBlocks(numBlocks)
}

main().catch((error) => {
  console.error("Fatal error:", error)
  process.exit(1)
})
