import { appendFileSync } from "node:fs"
import { getCurrentBlockNumber, insertEntitiesBatch, removeExpiredEntities } from "./db.js"
import { logBlockWarning, setTestName as setLoggerTestName } from "./logger.js"
import { writeQueue } from "./queue.js"
import type { PendingEntity } from "./types.js"

const PROCESSING_LOG_FILE = "processing.log"

let intervalId: NodeJS.Timeout | null = null
let testName: string = ""

function getDefaultTestName(): string {
  const now = new Date()
  const date = now.toISOString().split("T")[0].replace(/-/g, "")
  const hours = String(now.getHours()).padStart(2, "0")
  const minutes = String(now.getMinutes()).padStart(2, "0")
  return `perf_test_${date}_${hours}${minutes}`
}

function logToProcessingLog(message: string): void {
  try {
    appendFileSync(PROCESSING_LOG_FILE, message + "\n", "utf-8")
  } catch (error) {
    console.error("Failed to write to processing.log:", error)
  }
}

export function startBlockProcessor(testname?: string): void {
  if (intervalId) {
    console.log("Block processor already running")
    return
  }

  // Set testname: use provided value or generate default
  testName = testname || getDefaultTestName()
  
  // Set testname in logger so queries can use it
  setLoggerTestName(testName)

  const lastBlockNumber = getCurrentBlockNumber()
  console.log("Setting current block number to", lastBlockNumber)
  writeQueue.setCurrentBlockNumber(lastBlockNumber)

  // Log START line
  logToProcessingLog(`${testName} START ${lastBlockNumber}`)

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

function countAttributes(entities: PendingEntity[]): { stringCount: number; numericCount: number } {
  let stringCount = 0
  let numericCount = 0

  for (const entity of entities) {
    if (entity.stringAnnotations) {
      stringCount += Object.keys(entity.stringAnnotations).length
    }
    if (entity.numericAnnotations) {
      numericCount += Object.keys(entity.numericAnnotations).length
    }
  }

  return { stringCount, numericCount }
}

function processBlock(): void {
  const totalStartTime = performance.now()
  const pendingEntities = writeQueue.dequeueAll()

  if (pendingEntities.length === 0) {
    return // No entities to process
  }

  const blockNumber = writeQueue.getCurrentBlockNumber() - 1
  
  console.log(`Processing block ${blockNumber}: ${pendingEntities.length} entities`)

  // Time removeExpiredEntities separately
  const removeStartTime = performance.now()
  removeExpiredEntities(blockNumber)
  const removeDuration = performance.now() - removeStartTime

  // Count attributes
  const { stringCount, numericCount } = countAttributes(pendingEntities)

  try {
    // Time insertEntitiesBatch separately
    const insertStartTime = performance.now()
    insertEntitiesBatch(pendingEntities, blockNumber)
    const insertDuration = performance.now() - insertStartTime

    const totalDuration = performance.now() - totalStartTime
    const message = `[BLOCK] Block ${blockNumber} processed: ${pendingEntities.length} entities - ${totalDuration.toFixed(2)}ms`
    console.log(message)

    // Log to processing.log
    logToProcessingLog(
      `${testName} BLOCK ${blockNumber} ${Math.round(insertDuration)} ${Math.round(removeDuration)} ${Math.round(totalDuration)} ${pendingEntities.length} ${stringCount} ${numericCount}`
    )

    // Warn if block processing takes more than 1000ms
    if (totalDuration > 1000) {
      logBlockWarning(blockNumber, pendingEntities.length, totalDuration)
    }
  } catch (error) {
    const totalDuration = performance.now() - totalStartTime
    const errorMessage = `[BLOCK] Block ${blockNumber} error after ${totalDuration.toFixed(2)}ms: ${error}`
    console.error(errorMessage)
    // Entities are lost if transaction fails - in production, you might want to retry or persist the queue
  }
}
