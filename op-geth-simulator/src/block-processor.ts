import { getCurrentBlockNumber, insertEntitiesBatch, removeExpiredEntities } from "./db.js"
import { logBlockWarning } from "./logger.js"
import { writeQueue } from "./queue.js"

let intervalId: NodeJS.Timeout | null = null

export function startBlockProcessor(): void {
  if (intervalId) {
    console.log("Block processor already running")
    return
  }

  const lastBlockNumber = getCurrentBlockNumber()
  console.log("Setting current block number to", lastBlockNumber)
  writeQueue.setCurrentBlockNumber(lastBlockNumber)

  console.log("Starting block processor (processing every 2 seconds)...")

  intervalId = setInterval(() => {
    processBlock()
  }, 2000)
}

export function stopBlockProcessor(): void {
  if (intervalId) {
    clearInterval(intervalId)
    intervalId = null
    console.log("Block processor stopped")
  }
}

function processBlock(): void {
  const startTime = performance.now()
  const pendingEntities = writeQueue.dequeueAll()

  if (pendingEntities.length === 0) {
    return // No entities to process
  }

  const blockNumber = writeQueue.getCurrentBlockNumber() - 1
  
  console.log(`Processing block ${blockNumber}: ${pendingEntities.length} entities`)
  removeExpiredEntities(blockNumber)

  try {
    // Process all entities in a single transaction for atomicity
    insertEntitiesBatch(pendingEntities, blockNumber)
    const duration = performance.now() - startTime
    const message = `[BLOCK] Block ${blockNumber} processed: ${pendingEntities.length} entities - ${duration.toFixed(2)}ms`
    console.log(message)

    // Warn if block processing takes more than 1000ms
    if (duration > 1000) {
      logBlockWarning(blockNumber, pendingEntities.length, duration)
    }
  } catch (error) {
    const duration = performance.now() - startTime
    const errorMessage = `[BLOCK] Block ${blockNumber} error after ${duration.toFixed(2)}ms: ${error}`
    console.error(errorMessage)
    // Entities are lost if transaction fails - in production, you might want to retry or persist the queue
  }
}
