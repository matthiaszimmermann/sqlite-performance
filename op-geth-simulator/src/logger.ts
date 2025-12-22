import { appendFileSync } from "node:fs"

const LOG_FILE = "performance.log"

// ANSI color codes
const RESET = "\x1b[0m"
const YELLOW = "\x1b[33m"
const RED = "\x1b[31m"

function formatTimestamp(): string {
  return new Date().toISOString()
}

export function logToFile(message: string): void {
  const logLine = `[${formatTimestamp()}] ${message}\n`
  try {
    appendFileSync(LOG_FILE, logLine, "utf-8")
  } catch (error) {
    console.error("Failed to write to log file:", error)
  }
}

export function logQueryWarning(operation: string, duration: number): void {
  const message = `⚠️  SLOW QUERY: ${operation} took ${duration.toFixed(2)}ms (threshold: 200ms)`
  const coloredMessage = `${YELLOW}${message}${RESET}`
  console.warn(coloredMessage)
  logToFile(`[WARNING] ${message}`)
}

export function logRequestWarning(method: string, path: string, duration: number): void {
  const message = `⚠️  SLOW REQUEST: ${method} ${path} took ${duration}ms (threshold: 500ms)`
  const coloredMessage = `${RED}${message}${RESET}`
  console.warn(coloredMessage)
  logToFile(`[WARNING] ${message}`)
}

export function logBlockWarning(blockNumber: number, entityCount: number, duration: number): void {
  const message = `⚠️  SLOW BLOCK: Block ${blockNumber} processing ${entityCount} entities took ${duration.toFixed(2)}ms (threshold: 1000ms)`
  const coloredMessage = `${RED}${message}${RESET}`
  console.warn(coloredMessage)
  logToFile(`[WARNING] ${message}`)
}

const QUERY_LOG_FILE = "query.log"

let currentTestName: string = ""

export function setTestName(testName: string): void {
  currentTestName = testName
}

export function getTestName(): string {
  return currentTestName
}

function getDefaultTestName(): string {
  const now = new Date()
  const date = now.toISOString().split("T")[0].replace(/-/g, "")
  const hours = String(now.getHours()).padStart(2, "0")
  const minutes = String(now.getMinutes()).padStart(2, "0")
  return `perf_test_${date}_${hours}${minutes}`
}

export function logQuery(
  queryType: string,
  duration: number,
  params: Record<string, unknown>,
): void {
  // Use current test name or generate default
  const testName = currentTestName || getDefaultTestName()
  
  // Count the number of parameters (non-null/undefined params)
  const paramCount = Object.keys(params).length
  
  // Format: testname queryType duration_ms num_params
  // Similar to processing.log: <testname> <queryType> <duration_ms> <num_params>
  const logLine = `${testName} ${queryType} ${Math.round(duration)} ${paramCount}\n`
  
  try {
    appendFileSync(QUERY_LOG_FILE, logLine, "utf-8")
  } catch (error) {
    console.error("Failed to write to query.log:", error)
  }
}

