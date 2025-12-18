#!/usr/bin/env node

import { randomBytes } from "crypto"
import {
  cleanAllData,
  closeDatabase,
  initDatabase,
  insertEntitiesBatch,
  vacuumDatabase,
} from "./db.js"
import type { PendingEntity } from "./types.js"

function generateRandomBytes(sizeInKB: number): Buffer {
  const sizeInBytes = Math.floor(sizeInKB * 1024)
  return randomBytes(sizeInBytes)
}

function randomInt(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min
}

function randomAddress(): string {
  const chars = "0123456789abcdef"
  let address = "0x"
  for (let i = 0; i < 40; i++) {
    address += chars[Math.floor(Math.random() * chars.length)]
  }
  return address
}

// Predefined list of 20 words for string attribute values
const PREDEFINED_WORDS = [
  "alpha",
  "beta",
  "gamma",
  "delta",
  "epsilon",
  "zeta",
  "eta",
  "theta",
  "iota",
  "kappa",
  "lambda",
  "mu",
  "nu",
  "xi",
  "omicron",
  "pi",
  "rho",
  "sigma",
  "tau",
  "upsilon",
]

function randomWord(): string {
  return PREDEFINED_WORDS[Math.floor(Math.random() * PREDEFINED_WORDS.length)]
}

function generateAnnotations(numAttributes: number): {
  stringAnnotations: Record<string, string>
  numericAnnotations: Record<string, number>
} {
  const numStringAttrs = Math.floor(numAttributes / 2)
  const numNumericAttrs = numAttributes - numStringAttrs // Handle odd numbers

  const stringAnnotations: Record<string, string> = {}
  const numericAnnotations: Record<string, number> = {}

  // Generate string annotations with constant keys
  for (let i = 0; i < numStringAttrs; i++) {
    const key = `attr_str_${i}`
    // Random value from predefined list of 20 words
    stringAnnotations[key] = randomWord()
  }

  // Generate numeric annotations with constant keys
  for (let i = 0; i < numNumericAttrs; i++) {
    const key = `attr_num_${i}`
    // Random numeric value from 0 to 10
    numericAnnotations[key] = randomInt(0, 10)
  }

  return { stringAnnotations, numericAnnotations }
}

// Database path can be customized via environment variable
const DB_PATH = process.env.DB_PATH || "op-geth-sim.db"

async function addEntities(
  count: number,
  numAttributes: number,
  maxSizeKB: number = 120,
): Promise<void> {
  const numStringAttrs = Math.floor(numAttributes / 2)
  const numNumericAttrs = numAttributes - numStringAttrs

  console.log(
    `Adding ${count} entities with random payload sizes (0.5KB - ${maxSizeKB}KB) and ${numAttributes} attributes (${numStringAttrs} string, ${numNumericAttrs} numeric)...\n`,
  )

  // Initialize database
  console.log(`Connecting to database: ${DB_PATH}`)
  initDatabase(DB_PATH)

  const now = Math.floor(Date.now() / 1000)
  const startTime = Date.now()
  const BATCH_SIZE = 1000 // Insert in batches of 1000

  let currentBlock = 1
  let transactionIndex = 0
  let operationIndex = 0
  let entities: PendingEntity[] = []

  for (let i = 0; i < count; i++) {
    // Generate random payload size between 0.5KB and maxSizeKB
    const payloadSizeKB = 0.5 + Math.random() * (maxSizeKB - 0.5) // Random between 0.5 and maxSizeKB
    const payload = generateRandomBytes(payloadSizeKB)

    // Generate annotations with constant keys and random values
    const { stringAnnotations, numericAnnotations } = generateAnnotations(numAttributes)

    // Generate unique ID for this entity (similar to queue.enqueue)
    const id = `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`

    // Create entity object with ID (PendingEntity)
    const entity: PendingEntity = {
      id,
      key: `cli-entity-${Date.now()}-${i}-${Math.random().toString(36).substring(7)}`,
      expiresAt: now + randomInt(3600, 86400 * 7), // 1 hour to 7 days
      payload,
      contentType: "application/octet-stream",
      createdAtBlock: currentBlock,
      lastModifiedAtBlock: currentBlock,
      deleted: false,
      transactionIndexInBlock: transactionIndex,
      operationIndexInTransaction: operationIndex,
      ownerAddress: randomAddress(),
      stringAnnotations,
      numericAnnotations,
    }

    entities.push(entity)

    // Increment operation index, and transaction index if needed
    operationIndex++
    if (operationIndex >= 10) {
      // Reset operation index every 10 operations
      operationIndex = 0
      transactionIndex++
    }

    // Insert batch when we reach BATCH_SIZE or at the end
    if (entities.length >= BATCH_SIZE || i === count - 1) {
      try {
        insertEntitiesBatch(entities)
        const progress = ((i + 1) / count) * 100
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1)
        process.stdout.write(
          `\rProgress: ${i + 1}/${count} (${progress.toFixed(1)}%) - Inserted: ${entities.length} entities in batch - Elapsed: ${elapsed}s`,
        )
        entities = [] // Clear batch
        // Increment block number for next batch
        currentBlock++
        transactionIndex = 0
        operationIndex = 0
      } catch (error) {
        console.error(`\n✗ Error inserting batch:`, error)
        entities = [] // Clear batch even on error
      }
    }
  }

  console.log("\n")
  console.log(`✓ Completed: ${count} entities inserted directly into database`)
  const totalTime = ((Date.now() - startTime) / 1000).toFixed(2)
  const rate = (count / parseFloat(totalTime)).toFixed(0)
  console.log(`  Total time: ${totalTime}s`)
  console.log(`  Insert rate: ~${rate} entities/second`)
}

async function clean(): Promise<void> {
  console.log("Cleaning all data from the database...\n")

  // Initialize database
  console.log(`Connecting to database: ${DB_PATH}`)
  initDatabase(DB_PATH)

  try {
    cleanAllData()
    console.log("✓ All data has been cleaned from the database.")

    console.log("Running VACUUM to reclaim unused space...")
    vacuumDatabase()
    console.log("✓ Database vacuumed successfully.")
  } catch (error) {
    console.error("✗ Failed to clean data:", error)
    process.exit(1)
  }
}

function printUsage(): void {
  console.log(`
Usage: npm run cli <command> [options]

Commands:
  add <count> [attributes] [max-size]   Add N entities with random payload sizes
                                        and M attributes (half string, half numeric)
  clean                                 Clean all data from the database

Arguments:
  count                                 Number of entities to add
  attributes                            Number of attributes per entity (default: 10)
                                        Half will be string attributes, half numeric
  max-size                              Maximum payload size in KB (default: 120)
                                        Payload sizes will be random between 0.5KB and max-size

Examples:
  npm run cli add 100                   Add 100 entities with 10 attributes, max 120KB payload
  npm run cli add 100 20                Add 100 entities with 20 attributes, max 120KB payload
  npm run cli add 1000 50 50            Add 1000 entities with 50 attributes, max 50KB payload
  npm run cli add 10000 100 10          Add 10000 entities with 100 attributes, max 10KB payload
  npm run cli clean                     Clean all data

Environment variables:
  DB_PATH                               Database file path (default: op-geth-sim.db)
`)
}

async function main(): Promise<void> {
  const args = process.argv.slice(2)

  if (args.length === 0) {
    printUsage()
    process.exit(0)
  }

  const command = args[0]

  switch (command) {
    case "add": {
      const count = parseInt(args[1], 10)
      if (Number.isNaN(count) || count <= 0) {
        console.error("Error: Please provide a valid positive number for entity count")
        console.error("Example: npm run cli add 100")
        process.exit(1)
      }

      const numAttributes = args[2] ? parseInt(args[2], 10) : 10
      if (Number.isNaN(numAttributes) || numAttributes <= 0) {
        console.error("Error: Number of attributes must be a positive number")
        console.error("Example: npm run cli add 100 20")
        process.exit(1)
      }

      const maxSizeKB = args[3] ? parseFloat(args[3]) : 120
      if (Number.isNaN(maxSizeKB) || maxSizeKB < 0.5) {
        console.error("Error: Max size must be a positive number >= 0.5")
        console.error("Example: npm run cli add 100 20 50")
        process.exit(1)
      }

      await addEntities(count, numAttributes, maxSizeKB)
      break
    }

    case "clean": {
      await clean()
      break
    }

    case "help":
    case "--help":
    case "-h": {
      printUsage()
      break
    }

    default: {
      console.error(`Unknown command: ${command}`)
      printUsage()
      process.exit(1)
    }
  }
}

// Graceful shutdown
process.on("SIGINT", () => {
  console.log("\nShutting down...")
  closeDatabase()
  process.exit(0)
})

process.on("SIGTERM", () => {
  console.log("\nShutting down...")
  closeDatabase()
  process.exit(0)
})

main()
  .then(() => {
    closeDatabase()
  })
  .catch((error) => {
    console.error("Fatal error:", error)
    closeDatabase()
    process.exit(1)
  })
