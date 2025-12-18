#!/usr/bin/env node

import { Database } from "bun:sqlite"
import { randomBytes } from "crypto"
import { appendFileSync, existsSync, statSync, writeFileSync } from "fs"
import { performance } from "perf_hooks"

// Configuration
const SOURCE_DB = "mendoza.db"
const TARGET_DB = "output.db"
const BATCH_SIZE = 100 // Number of blocks to replicate before reporting stats
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

function writeReplicatedBlock(
  targetDb: Database,
  blockData: BlockData,
  newEntityKeyMap: Map<Buffer, Buffer>,
): void {
  const transaction = targetDb.transaction(() => {
    // Insert payloads
    const insertPayloadStmt = targetDb.prepare(`
      INSERT INTO payloads 
      (entity_key, from_block, to_block, payload, content_type, string_attributes, numeric_attributes)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `)

    for (const payload of blockData.payloads) {
      const newEntityKey = newEntityKeyMap.get(payload.entity_key) || generateNewEntityKey()
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
    const insertStringAttrStmt = targetDb.prepare(`
      INSERT INTO string_attributes 
      (entity_key, from_block, to_block, key, value)
      VALUES (?, ?, ?, ?, ?)
    `)

    for (const attr of blockData.stringAttributes) {
      const newEntityKey = newEntityKeyMap.get(attr.entity_key) || generateNewEntityKey()
      insertStringAttrStmt.run(newEntityKey, attr.from_block, attr.to_block, attr.key, attr.value)
    }

    // Insert numeric attributes
    const insertNumericAttrStmt = targetDb.prepare(`
      INSERT INTO numeric_attributes 
      (entity_key, from_block, to_block, key, value)
      VALUES (?, ?, ?, ?, ?)
    `)

    for (const attr of blockData.numericAttributes) {
      const newEntityKey = newEntityKeyMap.get(attr.entity_key) || generateNewEntityKey()
      insertNumericAttrStmt.run(newEntityKey, attr.from_block, attr.to_block, attr.key, attr.value)
    }
  })

  transaction()
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

async function replicateBlocks(
  numBlocks: number = Infinity,
  reportInterval: number = BATCH_SIZE,
): Promise<void> {
  console.log("Opening source database (read-only)...")
  const sourceDb = new Database(SOURCE_DB)

  console.log("Opening target database...")
  const targetDb = new Database(TARGET_DB)
  targetDb.exec("PRAGMA journal_mode = wal2")
  targetDb.exec("PRAGMA foreign_keys = OFF")
  targetDb.exec("PRAGMA synchronous = NORMAL")
  targetDb.exec("PRAGMA page_size = 8192") //  -- or 16384
  targetDb.exec("PRAGMA cache_size = -64000") // 64MB cache
  targetDb.exec("PRAGMA wal_autocheckpoint = 0") // Checkpoint every 10,000 pages

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

  // Get available blocks
  console.log("Reading available blocks from source database...")
  const availableBlocks = getAvailableBlocks(sourceDb)
  console.log(`Found ${availableBlocks.length} blocks in source database`)

  if (availableBlocks.length === 0) {
    console.error("No blocks found in source database!")
    sourceDb.close()
    targetDb.close()
    process.exit(1)
  }

  // Initialize CSV log file
  console.log(`Initializing CSV log file: ${CSV_LOG_FILE}`)
  initializeCsvLog()

  let blocksReplicated = 0
  let totalPayloads = 0
  let totalStringAttrs = 0
  let totalNumericAttrs = 0
  const startTime = performance.now()
  const readTimes: number[] = []
  const writeTimes: number[] = []
  const totalBlockTimes: number[] = []

  console.log("\nStarting block replication...")
  console.log("Press Ctrl+C to stop\n")

  try {
    while (blocksReplicated < numBlocks) {
      // Randomly select a block
      const randomIndex = Math.floor(Math.random() * availableBlocks.length)
      const selectedBlock = availableBlocks[randomIndex]

      const blockStartTime = performance.now()

      // Measure read time
      const readStartTime = performance.now()
      const blockData = readBlockData(sourceDb, selectedBlock)
      const readDuration = performance.now() - readStartTime
      readTimes.push(readDuration)

      if (
        blockData.payloads.length === 0 &&
        blockData.stringAttributes.length === 0 &&
        blockData.numericAttributes.length === 0
      ) {
        console.log(`Block ${selectedBlock} is empty, skipping...`)
        continue
      }

      // Create entity key mapping
      const entityKeyMap = createEntityKeyMap(blockData)

      // Measure write time
      const writeStartTime = performance.now()
      writeReplicatedBlock(targetDb, blockData, entityKeyMap)
      // check if WAL file exists
      let walSize = 0
      if (existsSync(TARGET_DB + "-wal2")) {
        walSize = statSync(TARGET_DB + "-wal2").size
      }
      if (existsSync(TARGET_DB + "-wal")) {
        walSize += statSync(TARGET_DB + "-wal").size
      }

      let writeDuration = performance.now() - writeStartTime
      if (walSize > 1024 * 1024) {
        console.log(`WAL file size: ${(walSize / 1024 / 1024).toFixed(2)} MB`)
        console.log(`write time without WAL sync: ${writeDuration.toFixed(1)}ms`)
      }
      targetDb.exec("PRAGMA wal_checkpoint(TRUNCATE)")

      writeDuration = performance.now() - writeStartTime
      writeTimes.push(writeDuration)

      if (walSize > 1024 * 1024) {
        console.log(`write time with WAL sync: ${writeDuration.toFixed(1)}ms`)
      }

      const blockDuration = performance.now() - blockStartTime
      totalBlockTimes.push(blockDuration)

      blocksReplicated++
      totalPayloads += blockData.payloads.length
      totalStringAttrs += blockData.stringAttributes.length
      totalNumericAttrs += blockData.numericAttributes.length

      // Write CSV log entry
      const outputDbSize = getOutputDbSize()
      writeCsvRow(
        blockData.payloads.length,
        blockData.stringAttributes.length,
        blockData.numericAttributes.length,
        readDuration,
        writeDuration,
        outputDbSize,
      )

      // Report progress
      if (blocksReplicated % reportInterval === 0) {
        const elapsed = (performance.now() - startTime) / 1000
        const recentReadTimes = readTimes.slice(-reportInterval)
        const recentWriteTimes = writeTimes.slice(-reportInterval)
        const recentTotalTimes = totalBlockTimes.slice(-reportInterval)

        const avgReadTime = recentReadTimes.reduce((a, b) => a + b, 0) / reportInterval
        const avgWriteTime = recentWriteTimes.reduce((a, b) => a + b, 0) / reportInterval
        const avgTotalTime = recentTotalTimes.reduce((a, b) => a + b, 0) / reportInterval
        const blocksPerSecond = reportInterval / (elapsed / blocksReplicated)

        console.log(
          `\n[Progress] Blocks: ${blocksReplicated} | ` +
            `Payloads: ${totalPayloads} | ` +
            `String Attrs: ${totalStringAttrs} | ` +
            `Numeric Attrs: ${totalNumericAttrs}`,
        )
        console.log(
          `[Performance] Total: ${avgTotalTime.toFixed(2)}ms | ` +
            `Read: ${avgReadTime.toFixed(2)}ms | ` +
            `Write: ${avgWriteTime.toFixed(2)}ms | ` +
            `Blocks/sec: ${blocksPerSecond.toFixed(2)} | ` +
            `Elapsed: ${elapsed.toFixed(1)}s`,
        )

        // Check for performance degradation
        if (readTimes.length >= reportInterval * 2) {
          const recentReadAvg = avgReadTime
          const earlierReadAvg =
            readTimes.slice(-reportInterval * 2, -reportInterval).reduce((a, b) => a + b, 0) /
            reportInterval

          const recentWriteAvg = avgWriteTime
          const earlierWriteAvg =
            writeTimes.slice(-reportInterval * 2, -reportInterval).reduce((a, b) => a + b, 0) /
            reportInterval

          if (recentReadAvg > earlierReadAvg * 1.5) {
            console.log(
              `⚠️  WARNING: Read performance degradation! ` +
                `Recent: ${recentReadAvg.toFixed(2)}ms vs Earlier: ${earlierReadAvg.toFixed(2)}ms`,
            )
          }

          if (recentWriteAvg > earlierWriteAvg * 1.5) {
            console.log(
              `⚠️  WARNING: Write performance degradation! ` +
                `Recent: ${recentWriteAvg.toFixed(2)}ms vs Earlier: ${earlierWriteAvg.toFixed(2)}ms`,
            )
          }
        }
      } else {
        //Show brief progress
        if (writeDuration > 1000) {
          // show size of WAL file checking file size on disk
          process.stdout.write(
            `\r[${blocksReplicated}] Block ${selectedBlock} (${blockData.payloads.length} payloads, ` +
              `${blockData.stringAttributes.length} str attrs, ` +
              `${blockData.numericAttributes.length} num attrs) | ` +
              `Read: ${readDuration.toFixed(1)}ms | ` +
              `Write: ${writeDuration.toFixed(1)}ms | ` +
              `Total: ${blockDuration.toFixed(1)}ms | ` +
              `WAL file size: ${(walSize / 1024 / 1024).toFixed(2)} MB`,
          )
        }
      }
    }
  } catch (error) {
    console.error("\nError during replication:", error)
    throw error
  } finally {
    const totalTime = (performance.now() - startTime) / 1000
    const finalAvgReadTime =
      readTimes.length > 0 ? readTimes.reduce((a, b) => a + b, 0) / readTimes.length : 0
    const finalAvgWriteTime =
      writeTimes.length > 0 ? writeTimes.reduce((a, b) => a + b, 0) / writeTimes.length : 0
    const finalAvgTotalTime =
      totalBlockTimes.length > 0
        ? totalBlockTimes.reduce((a, b) => a + b, 0) / totalBlockTimes.length
        : 0

    console.log("\n\n=== Replication Complete ===")
    console.log(`Total blocks replicated: ${blocksReplicated}`)
    console.log(`Total payloads: ${totalPayloads}`)
    console.log(`Total string attributes: ${totalStringAttrs}`)
    console.log(`Total numeric attributes: ${totalNumericAttrs}`)
    console.log(`Total time: ${totalTime.toFixed(2)}s`)
    console.log(`\n=== Average Times ===`)
    console.log(`Total block time: ${finalAvgTotalTime.toFixed(2)}ms`)
    console.log(`Read time: ${finalAvgReadTime.toFixed(2)}ms`)
    console.log(`Write time: ${finalAvgWriteTime.toFixed(2)}ms`)
    console.log(`Blocks per second: ${(blocksReplicated / totalTime).toFixed(2)}`)

    // Performance analysis for read times
    if (readTimes.length > 0) {
      const sortedReadTimes = [...readTimes].sort((a, b) => a - b)
      const readP50 = sortedReadTimes[Math.floor(sortedReadTimes.length * 0.5)]
      const readP95 = sortedReadTimes[Math.floor(sortedReadTimes.length * 0.95)]
      const readP99 = sortedReadTimes[Math.floor(sortedReadTimes.length * 0.99)]

      console.log("\n=== Read Performance Percentiles ===")
      console.log(`P50 (median): ${readP50.toFixed(2)}ms`)
      console.log(`P95: ${readP95.toFixed(2)}ms`)
      console.log(`P99: ${readP99.toFixed(2)}ms`)
      console.log(`Min: ${Math.min(...readTimes).toFixed(2)}ms`)
      console.log(`Max: ${Math.max(...readTimes).toFixed(2)}ms`)
    }

    // Performance analysis for write times
    if (writeTimes.length > 0) {
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

    // Performance analysis for total block times
    if (totalBlockTimes.length > 0) {
      const sortedTotalTimes = [...totalBlockTimes].sort((a, b) => a - b)
      const totalP50 = sortedTotalTimes[Math.floor(sortedTotalTimes.length * 0.5)]
      const totalP95 = sortedTotalTimes[Math.floor(sortedTotalTimes.length * 0.95)]
      const totalP99 = sortedTotalTimes[Math.floor(sortedTotalTimes.length * 0.99)]

      console.log("\n=== Total Block Performance Percentiles ===")
      console.log(`P50 (median): ${totalP50.toFixed(2)}ms`)
      console.log(`P95: ${totalP95.toFixed(2)}ms`)
      console.log(`P99: ${totalP99.toFixed(2)}ms`)
      console.log(`Min: ${Math.min(...totalBlockTimes).toFixed(2)}ms`)
      console.log(`Max: ${Math.max(...totalBlockTimes).toFixed(2)}ms`)
    }

    sourceDb.close()
    targetDb.close()
  }
}

// Main execution
async function main(): Promise<void> {
  const args = process.argv.slice(2)
  const numBlocks = args[0] ? parseInt(args[0], 10) : Infinity
  const reportInterval = args[1] ? parseInt(args[1], 10) : BATCH_SIZE

  if (args[0] && (Number.isNaN(numBlocks) || numBlocks <= 0)) {
    console.error("Error: Number of blocks must be a positive number")
    console.error("Usage: tsx src/block-replicator.ts [num_blocks] [report_interval]")
    console.error("Example: tsx src/block-replicator.ts 1000 100")
    process.exit(1)
  }

  await replicateBlocks(numBlocks, reportInterval)
}

main().catch((error) => {
  console.error("Fatal error:", error)
  process.exit(1)
})
