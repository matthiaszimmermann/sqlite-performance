import { serve } from "@hono/node-server"
import { Hono } from "hono"
import { startBlockProcessor, stopBlockProcessor } from "./block-processor.js"
import {
  cleanAllData,
  closeDatabase,
  countEntities,
  getEntityByKey,
  getReceiptById,
  initDatabase,
  queryEntities,
} from "./db.js"
import { logRequestWarning } from "./logger.js"
import { writeQueue } from "./queue.js"
import type { EntityQueryRequest, EntityWriteRequest } from "./types.js"

// Parse command-line arguments for DB path
function parseDbPath(): string | undefined {
  const args = process.argv.slice(2)
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--db-path" && i + 1 < args.length) {
      return args[i + 1]
    }
    if (args[i].startsWith("--db-path=")) {
      return args[i].split("=")[1]
    }
  }
  return undefined
}

// Parse command-line arguments for test name
function parseTestName(): string | undefined {
  const args = process.argv.slice(2)
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--testname" && i + 1 < args.length) {
      return args[i + 1]
    }
    if (args[i].startsWith("--testname=")) {
      return args[i].split("=")[1]
    }
  }
  return undefined
}

const dbPath = parseDbPath()
const testName = parseTestName()

// Initialize database
initDatabase(dbPath)

// Start block processor
startBlockProcessor(testName)

// Graceful shutdown
process.on("SIGINT", () => {
  console.log("\nShutting down...")
  stopBlockProcessor()
  closeDatabase()
  process.exit(0)
})

process.on("SIGTERM", () => {
  console.log("\nShutting down...")
  stopBlockProcessor()
  closeDatabase()
  process.exit(0)
})

const app = new Hono()

// Middleware to measure and log request time
app.use("*", async (c, next) => {
  const startTime = Date.now()
  const method = c.req.method
  const path = c.req.path

  await next()

  const duration = Date.now() - startTime
  const status = c.res.status
  if (status >= 400) {
    console.error(await c.req.text())
  }
  

  console.log(`[${new Date().toISOString()}] ${method} ${path} - ${status} - ${duration}ms`)

  // Warn if request takes more than 500ms
  if (duration > 500) {
    logRequestWarning(method, path, duration)
  }
})

// Health check
app.get("/health", (c) => {
  return c.json({ status: "ok", queueSize: writeQueue.getQueueSize() })
})

// 1. Write entity endpoint
app.post("/entities", async (c) => {
  try {
    const body = (await c.req.json()) as EntityWriteRequest

    // Validate required fields
    if (!body.key || !body.contentType || !body.ownerAddress) {
      return c.json({ error: "Missing required fields: key, contentType, ownerAddress" }, 400)
    }

    if (body.expiresIn === undefined || body.expiresIn === null) {
      return c.json({ error: "Missing required field: expiresIn" }, 400)
    }
    
    if (body.expiresIn <= 0) {
      return c.json({ error: "expiresIn must be a positive number" }, 400)
    }

    // Enqueue the entity (doesn't write to DB immediately)
    const id = writeQueue.enqueue(body)

    return c.json(
      {
        success: true,
        id,
        message: "Entity queued for processing",
        queueSize: writeQueue.getQueueSize(),
      },
      202,
    ) // 202 Accepted - request accepted but not yet processed
  } catch (error) {
    console.error("Error in write entity endpoint:", error)
    return c.json({ error: "Internal server error" }, 500)
  }
})

// 2. Get entity by key endpoint
app.get("/entities/:key", (c) => {
  try {
    const key = c.req.param("key")

    if (!key) {
      return c.json({ error: "Key parameter is required" }, 400)
    }

    const entity = getEntityByKey(key)

    if (!entity) {
      return c.json({ error: "Entity not found" }, 404)
    }

    // Convert Buffer to base64 string if payload exists
    const response = {
      ...entity,
      payload:
        entity.payload instanceof Buffer ? entity.payload.toString("base64") : entity.payload,
    }

    return c.json(response)
  } catch (error) {
    console.error("Error in get entity endpoint:", error)
    return c.json({ error: "Internal server error" }, 500)
  }
})

// 3. Query entities by attributes endpoint
app.post("/entities/query", async (c) => {
  try {
    const body = (await c.req.json()) as EntityQueryRequest

    const entities = await queryEntities(
      body.ownerAddress,
      body.stringAnnotations,
      body.numericAnnotations,
      body.limit || 100,
      body.offset || 0,
    )

    // Convert Buffer payloads to base64 strings
    const response = entities.map((entity) => ({
      ...entity,
      payload:
        entity.payload instanceof Buffer ? entity.payload.toString("base64") : entity.payload,
    }))

    return c.json({
      entities: response,
      count: response.length,
    })
  } catch (error) {
    console.error("Error in query entities endpoint:", error)
    return c.json({ error: "Internal server error" }, 500)
  }
})

// 4. Count all entities endpoint
app.get("/entities/count", (c) => {
  try {
    const count = countEntities()
    return c.json({ count })
  } catch (error) {
    console.error("Error in count entities endpoint:", error)
    return c.json({ error: "Internal server error" }, 500)
  }
})

// 5. Clean all data endpoint
app.delete("/entities/clean", (c) => {
  try {
    cleanAllData()
    return c.json({ success: true, message: "All data cleaned" })
  } catch (error) {
    console.error("Error in clean all data endpoint:", error)
    return c.json({ error: "Internal server error" }, 500)
  }
})

// 6. Get receipt endpoint - check if entity was saved to DB (like Ethereum's getReceipt)
app.get("/receipt/:id", (c) => {
  try {
    const id = c.req.param("id")

    if (!id) {
      return c.json({ error: "ID parameter is required" }, 400)
    }

    // Query receipt directly from database (no queue lookup)
    const receipt = getReceiptById(id)

    if (!receipt) {
      return c.json({ error: "Receipt not found" }, 404)
    }

    // Receipt found - entity is saved in DB
    return c.json({
      id: receipt.id,
      key: receipt.key,
      createdAtBlock: receipt.createdAtBlock,
    })
  } catch (error) {
    console.error("Error in receipt endpoint:", error)
    return c.json({ error: "Internal server error" }, 500)
  }
})

const port = process.env.PORT ? parseInt(process.env.PORT) : 3000

console.log(`Server starting on port ${port}...`)

serve(
  {
    fetch: app.fetch,
    port,
  },
  (info) => {
    console.log(`Server running on http://localhost:${info.port}`)
  },
)
