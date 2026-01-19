package main

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"sync"
	"time"
)

// WriteQueue manages a queue of pending entities
type WriteQueue struct {
	mu                 sync.Mutex
	createQueue        []*PendingEntity
	updateQueue        []*PendingEntity
	currentBlockNumber int64
	transactionIndex   int
	operationIndex     int
}

var writeQueue = &WriteQueue{
	currentBlockNumber: 1,
}

// Enqueue adds an entity to the queue
func (q *WriteQueue) EnqueueCreate(request *EntityCreateRequest) string {
	q.mu.Lock()
	defer q.mu.Unlock()

	// Generate unique ID
	id := fmt.Sprintf("%d-%s", time.Now().UnixNano(), randomString(9))

	// Get the latest entity for this key to determine block numbers
	lastModifiedAtBlock := q.currentBlockNumber
	createdAtBlock := lastModifiedAtBlock

	// Convert expiresIn (blocks from current) to expiresAt (absolute block number)
	expiresAt := q.currentBlockNumber + request.ExpiresIn

	// Decode payload if provided
	var payload []byte
	if request.Payload != "" {
		decoded, err := base64.StdEncoding.DecodeString(request.Payload)
		if err != nil {
			// If base64 decode fails, treat as plain string
			payload = []byte(request.Payload)
		} else {
			payload = decoded
		}
	}

	// Convert numeric annotations from interface{} to float64
	numericAnnotations := make(map[string]float64)
	if request.NumericAnnotations != nil {
		for k, v := range request.NumericAnnotations {
			switch val := v.(type) {
			case float64:
				numericAnnotations[k] = val
			case int:
				numericAnnotations[k] = float64(val)
			case string:
				// Range queries are handled in query, not here
				// For now, try to parse as number
				var f float64
				if _, err := fmt.Sscanf(val, "%f", &f); err == nil {
					numericAnnotations[k] = f
				}
			}
		}
	}

	entity := &PendingEntity{
		ID: id,
		Entity: Entity{
			Key:                         request.Key,
			ExpiresAt:                   expiresAt,
			Payload:                     payload,
			ContentType:                 request.ContentType,
			CreatedAtBlock:              createdAtBlock,
			LastModifiedAtBlock:         lastModifiedAtBlock,
			Deleted:                     request.Deleted,
			TransactionIndexInBlock:     q.transactionIndex,
			OperationIndexInTransaction: q.operationIndex,
			OwnerAddress:                request.OwnerAddress,
			StringAnnotations:           request.StringAnnotations,
			NumericAnnotations:          numericAnnotations,
		},
	}

	q.createQueue = append(q.createQueue, entity)

	// Increment operation index, and transaction index if needed
	q.operationIndex++
	if q.operationIndex >= 10 {
		// Reset operation index every 10 operations
		q.operationIndex = 0
		q.transactionIndex++
	}

	return id
}

// EnqueueUpdate adds an update operation to the queue.
func (q *WriteQueue) EnqueueUpdate(request *EntityUpdateRequest) string {
	q.mu.Lock()
	defer q.mu.Unlock()

	// Generate unique ID
	id := fmt.Sprintf("%d-%s", time.Now().UnixNano(), randomString(9))

	lastModifiedAtBlock := q.currentBlockNumber
	createdAtBlock := lastModifiedAtBlock

	expiresAt := q.currentBlockNumber + request.ExpiresIn

	// Decode payload if provided
	var payload []byte
	if request.Payload != "" {
		decoded, err := base64.StdEncoding.DecodeString(request.Payload)
		if err != nil {
			payload = []byte(request.Payload)
		} else {
			payload = decoded
		}
	}

	// Convert numeric annotations from interface{} to float64
	numericAnnotations := make(map[string]float64)
	if request.NumericAnnotations != nil {
		for k, v := range request.NumericAnnotations {
			switch val := v.(type) {
			case float64:
				numericAnnotations[k] = val
			case int:
				numericAnnotations[k] = float64(val)
			case string:
				var f float64
				if _, err := fmt.Sscanf(val, "%f", &f); err == nil {
					numericAnnotations[k] = f
				}
			}
		}
	}

	entity := &PendingEntity{
		ID: id,
		Entity: Entity{
			Key:                         request.Key,
			ExpiresAt:                   expiresAt,
			Payload:                     payload,
			ContentType:                 request.ContentType,
			CreatedAtBlock:              createdAtBlock,
			LastModifiedAtBlock:         lastModifiedAtBlock,
			Deleted:                     request.Deleted,
			TransactionIndexInBlock:     q.transactionIndex,
			OperationIndexInTransaction: q.operationIndex,
			OwnerAddress:                request.OwnerAddress,
			StringAnnotations:           request.StringAnnotations,
			NumericAnnotations:          numericAnnotations,
		},
	}

	q.updateQueue = append(q.updateQueue, entity)

	q.operationIndex++
	if q.operationIndex >= 10 {
		q.operationIndex = 0
		q.transactionIndex++
	}

	return id
}

// DequeueAll removes and returns all pending create and update operations.
func (q *WriteQueue) DequeueAll() (creates []*PendingEntity, updates []*PendingEntity) {
	q.mu.Lock()
	defer q.mu.Unlock()

	creates = make([]*PendingEntity, len(q.createQueue))
	copy(creates, q.createQueue)
	updates = make([]*PendingEntity, len(q.updateQueue))
	copy(updates, q.updateQueue)

	q.createQueue = q.createQueue[:0]
	q.updateQueue = q.updateQueue[:0]

	q.transactionIndex = 0
	q.operationIndex = 0
	if len(creates) > 0 || len(updates) > 0 {
		q.currentBlockNumber++
	}
	return creates, updates
}

// GetQueueSize returns the current queue size
func (q *WriteQueue) GetQueueSize() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	return len(q.createQueue) + len(q.updateQueue)
}

// GetCurrentBlockNumber returns the current block number
func (q *WriteQueue) GetCurrentBlockNumber() int64 {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.currentBlockNumber
}

// SetCurrentBlockNumber sets the current block number
func (q *WriteQueue) SetCurrentBlockNumber(blockNumber int64) {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.currentBlockNumber = blockNumber
}

// randomString generates a random string of the given length
func randomString(length int) string {
	b := make([]byte, (length+3)/4*3) // Ensure we have enough bytes
	rand.Read(b)
	encoded := base64.URLEncoding.EncodeToString(b)
	if len(encoded) > length {
		return encoded[:length]
	}
	return encoded
}
