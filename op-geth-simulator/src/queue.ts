import type { EntityWriteRequest, PendingEntity } from "./types.js"

class WriteQueue {
  private queue: PendingEntity[] = []
  private currentBlockNumber: number = 1
  private transactionIndex: number = 0
  private operationIndex: number = 0

  enqueue(request: EntityWriteRequest): string {
    const id = `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`

    // Get the latest entity for this key to determine block numbers
    // For now, we'll use the current block number
    const lastModifiedAtBlock = this.currentBlockNumber
    const createdAtBlock = lastModifiedAtBlock // Simplified: assume new entity
    
    // Convert expiresIn (blocks from current) to expiresAt (absolute block number)
    const expiresAt = this.currentBlockNumber + request.expiresIn

    const entity: PendingEntity = {
      id,
      key: request.key,
      expiresAt: expiresAt,
      payload: request.payload ? Buffer.from(request.payload, "base64") : undefined,
      contentType: request.contentType,
      createdAtBlock,
      lastModifiedAtBlock,
      deleted: request.deleted || false,
      transactionIndexInBlock: this.transactionIndex,
      operationIndexInTransaction: this.operationIndex,
      ownerAddress: request.ownerAddress,
      stringAnnotations: request.stringAnnotations,
      numericAnnotations: request.numericAnnotations,
    }

    this.queue.push(entity)

    // Increment operation index, and transaction index if needed
    this.operationIndex++
    if (this.operationIndex >= 10) {
      // Reset operation index every 10 operations
      this.operationIndex = 0
      this.transactionIndex++
    }

    return id
  }

  dequeueAll(): PendingEntity[] {
    const entities = [...this.queue]
    this.queue = []
    this.transactionIndex = 0
    this.operationIndex = 0
    if (entities.length > 0) {
      this.currentBlockNumber++
    }
    return entities
  }

  getQueueSize(): number {
    return this.queue.length
  }

  getCurrentBlockNumber(): number {
    return this.currentBlockNumber
  }

  setCurrentBlockNumber(blockNumber: number): void {
    this.currentBlockNumber = blockNumber
  }
}

export const writeQueue = new WriteQueue()
