package main

import (
	"context"
	"crypto/sha256"
	"fmt"
	"sync"
	"time"

	arkivevents "github.com/Arkiv-Network/arkiv-events"
	"github.com/Arkiv-Network/arkiv-events/events"
	"github.com/Arkiv-Network/sqlite-bitmap-store/pusher"
	"github.com/ethereum/go-ethereum/common"
)

var (
	intervalID         *time.Ticker
	processorMutex     sync.Mutex
	testName           string
	pushIterator       *pusher.PushIterator
	followEventsCtx    context.Context
	followEventsCancel context.CancelFunc
)

// StartBlockProcessor starts the block processor that runs every 2 seconds
func StartBlockProcessor(testname string) {
	processorMutex.Lock()
	defer processorMutex.Unlock()

	if intervalID != nil {
		fmt.Println("Block processor already running")
		return
	}

	// Set testname: use provided value or generate default
	if testname == "" {
		testName = getDefaultTestName()
	} else {
		testName = testname
	}

	// Set testname in logger so queries can use it
	SetTestName(testName)

	lastBlockNumber := GetCurrentBlockNumber()
	newBlockNumber := lastBlockNumber + 1
	fmt.Printf("Setting current block number to %d\n", newBlockNumber)
	writeQueue.SetCurrentBlockNumber(newBlockNumber)

	// Log START line
	logToProcessingLog(fmt.Sprintf("%s START %d", testName, newBlockNumber))

	fmt.Println("Starting block processor (processing every 2 seconds)...")

	// Create a shared PushIterator for all blocks
	pushIterator = pusher.NewPushIterator()

	// Create context for FollowEvents
	followEventsCtx, followEventsCancel = context.WithCancel(context.Background())

	// Start FollowEvents in a separate goroutine - it will run continuously
	// and process batches as they're pushed to the iterator
	go func() {
		fmt.Println("[FOLLOW] Starting FollowEvents goroutine...")
		batchIterator := pushIterator.Iterator()
		if err := FollowEvents(followEventsCtx, arkivevents.BatchIterator(batchIterator)); err != nil {
			if err != context.Canceled {
				fmt.Printf("[FOLLOW] FollowEvents error: %v\n", err)
			} else {
				fmt.Println("[FOLLOW] FollowEvents stopped (context canceled)")
			}
		}
	}()

	// Create ticker for 2 second intervals
	intervalID = time.NewTicker(2 * time.Second)

	go func() {
		tickCount := 0
		for range intervalID.C {
			tickCount++
			// Wrap processBlock in a recover to prevent crashes from stopping the ticker
			func() {
				defer func() {
					if r := recover(); r != nil {
						fmt.Printf("[ERROR] Panic in processBlock: %v\n", r)
					}
				}()
				processBlock()
			}()
		}
	}()
}

// StopBlockProcessor stops the block processor
func StopBlockProcessor() {
	processorMutex.Lock()
	defer processorMutex.Unlock()

	if intervalID != nil {
		intervalID.Stop()
		intervalID = nil
	}

	// Cancel FollowEvents context and close iterator
	if followEventsCancel != nil {
		followEventsCancel()
	}
	if pushIterator != nil {
		pushIterator.Close()
		pushIterator = nil
	}

	fmt.Println("Block processor stopped")
}

// countAttributes counts string and numeric attributes in entities
func countAttributes(entities []*PendingEntity) (stringCount, numericCount int) {
	for _, entity := range entities {
		if entity.StringAnnotations != nil {
			stringCount += len(entity.StringAnnotations)
		}
		if entity.NumericAnnotations != nil {
			numericCount += len(entity.NumericAnnotations)
		}
	}
	return
}

// processBlock processes all pending entities in a batch
func processBlock() {
	totalStartTime := time.Now()

	// Get block number BEFORE dequeuing (since DequeueAll increments it)
	blockNumber := writeQueue.GetCurrentBlockNumber()
	pendingEntities := writeQueue.DequeueAll()

	if len(pendingEntities) == 0 {
		// Still log that we're checking, but less frequently
		queueSize := writeQueue.GetQueueSize()
		if queueSize > 0 {
			logBlockInfoMsg(blockNumber, "No entities to process yet (queue size: %d)", queueSize)
		}
		return // No entities to process
	}

	logBlockInfoMsg(blockNumber, "Processing %d entities", len(pendingEntities))
	logBlockDebug(blockNumber, "Starting to build events...")

	// Count attributes
	stringCount, numericCount := countAttributes(pendingEntities)
	logBlockDebug(blockNumber, "Attributes counted - string: %d, numeric: %d", stringCount, numericCount)

	// Create a single block for all events in this block number
	block := events.Block{
		Number:     uint64(blockNumber),
		Operations: []events.Operation{},
	}
	ctx := context.Background()

	// Time removeExpiredEntities separately and create delete events
	removeStartTime := time.Now()
	logBlockDebug(blockNumber, "Getting expired entities...")
	expiredEntities, err := GetExpiredEntities(blockNumber)
	if err != nil {
		logBlockDebug(blockNumber, "Error getting expired entities: %v", err)
	} else {
		logBlockDebug(blockNumber, "Found %d expired entities", len(expiredEntities))
		// Add delete operations for expired entities to the block
		for _, entity := range expiredEntities {
			// Convert entity key to Hash (32 bytes)
			keyHash := sha256.Sum256([]byte(entity.Key))
			deleteOp := events.Operation{
				Delete: (*events.OPDelete)(&keyHash),
			}
			block.Operations = append(block.Operations, deleteOp)
		}
	}
	removeDuration := time.Since(removeStartTime)
	logBlockDebug(blockNumber, "Expired entities processed in %v", removeDuration)

	// Time insertEntitiesBatch separately and create create events
	insertStartTime := time.Now()
	logBlockDebug(blockNumber, "Creating create events for %d entities...", len(pendingEntities))
	for i, pendingEntity := range pendingEntities {
		entity := &pendingEntity.Entity
		entity.CreatedAtBlock = blockNumber
		entity.LastModifiedAtBlock = blockNumber

		// Create create event for the entity
		entityKey := []byte(entity.Key)
		var payload []byte
		if len(entity.Payload) > 0 {
			payload = entity.Payload
		}

		// Convert entity key to Hash (32 bytes)
		keyHash := sha256.Sum256(entityKey)

		// Convert owner address from hex string
		var ownerAddr common.Address
		if entity.OwnerAddress != "" {
			ownerAddr = common.HexToAddress(entity.OwnerAddress)
		}

		// Extract string attributes
		stringAttrs := make(map[string]string)
		if entity.StringAnnotations != nil {
			stringAttrs = entity.StringAnnotations
		}

		// Extract numeric attributes and convert to uint64
		numericAttrs := make(map[string]uint64)
		if entity.NumericAnnotations != nil {
			for k, v := range entity.NumericAnnotations {
				numericAttrs[k] = uint64(v)
			}
		}

		// Calculate transaction and operation indices (10 operations per transaction)
		txIndex := uint64(i / 10)
		opIndex := uint64(i % 10)

		// Add create operation to the block
		createOp := events.Operation{
			TxIndex: txIndex,
			OpIndex: opIndex,
			Create: &events.OPCreate{
				Key:               common.Hash(keyHash),
				ContentType:       entity.ContentType,
				BTL:               uint64(entity.ExpiresAt - entity.LastModifiedAtBlock),
				Owner:             ownerAddr,
				Content:           payload,
				StringAttributes:  stringAttrs,
				NumericAttributes: numericAttrs,
			},
		}
		block.Operations = append(block.Operations, createOp)
	}
	logBlockDebug(blockNumber, "Created %d total operations (%d creates, %d deletes)",
		len(block.Operations), len(pendingEntities), len(block.Operations)-len(pendingEntities))

	// Use pusher to create block event batches
	// Create BlockBatch with the single block
	logBlockDebug(blockNumber, "Creating BlockBatch...")
	blockBatch := events.BlockBatch{
		Blocks: []events.Block{block},
	}

	// Push the block batch to the shared PushIterator
	// FollowEvents (running in background) will pick it up automatically
	fmt.Printf("[BLOCK] Block %d: Pushing block batch with %d operations to iterator (block number: %d)\n",
		blockNumber, len(block.Operations), block.Number)

	// Log first operation details for debugging
	if len(block.Operations) > 0 {
		firstOp := block.Operations[0]
		if firstOp.Create != nil {
			logBlockDebug(blockNumber, "First operation is CREATE with key: %s, contentType: %s",
				firstOp.Create.Key.Hex(), firstOp.Create.ContentType)
		}
	}

	// Push the batch to the shared iterator - FollowEvents will process it
	pushIterator.Push(ctx, blockBatch)
	logBlockInfoMsg(blockNumber, "Block batch pushed to iterator, FollowEvents will process it")

	insertDuration := time.Since(insertStartTime)

	totalDuration := time.Since(totalStartTime)
	logBlockInfoMsg(blockNumber, "Processed %d entities - %.2fms", len(pendingEntities), totalDuration.Seconds()*1000)

	// Log to processing.log
	logToProcessingLog(
		fmt.Sprintf("%s BLOCK %d %d %d %d %d %d %d",
			testName,
			blockNumber,
			int(insertDuration.Milliseconds()),
			int(removeDuration.Milliseconds()),
			int(totalDuration.Milliseconds()),
			len(pendingEntities),
			stringCount,
			numericCount,
		),
	)

	// Warn if block processing takes more than 1000ms
	if totalDuration > 1000*time.Millisecond {
		logBlockWarning(blockNumber, len(pendingEntities), totalDuration)
	}
}
