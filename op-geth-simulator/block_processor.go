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
			fmt.Printf("[BLOCK] Block %d: No entities to process yet (queue size: %d)\n", blockNumber, queueSize)
		}
		return // No entities to process
	}

	fmt.Printf("Processing block %d: %d entities\n", blockNumber, len(pendingEntities))
	fmt.Printf("[DEBUG] Block %d: Starting to build events...\n", blockNumber)

	// Count attributes
	stringCount, numericCount := countAttributes(pendingEntities)
	fmt.Printf("[DEBUG] Block %d: Attributes counted - string: %d, numeric: %d\n", blockNumber, stringCount, numericCount)

	// Create a single block for all events in this block number
	block := events.Block{
		Number:     uint64(blockNumber),
		Operations: []events.Operation{},
	}
	ctx := context.Background()

	// Time removeExpiredEntities separately and create delete events
	removeStartTime := time.Now()
	fmt.Printf("[DEBUG] Block %d: Getting expired entities...\n", blockNumber)
	expiredEntities, err := GetExpiredEntities(blockNumber)
	if err != nil {
		fmt.Printf("[DEBUG] Block %d: Error getting expired entities: %v\n", blockNumber, err)
	} else {
		fmt.Printf("[DEBUG] Block %d: Found %d expired entities\n", blockNumber, len(expiredEntities))
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
	fmt.Printf("[DEBUG] Block %d: Expired entities processed in %v\n", blockNumber, removeDuration)

	// Time insertEntitiesBatch separately and create create events
	insertStartTime := time.Now()
	fmt.Printf("[DEBUG] Block %d: Creating create events for %d entities...\n", blockNumber, len(pendingEntities))
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
	fmt.Printf("[DEBUG] Block %d: Created %d total operations (%d creates, %d deletes)\n",
		blockNumber, len(block.Operations), len(pendingEntities), len(block.Operations)-len(pendingEntities))

	// Use pusher to create block event batches
	// Create BlockBatch with the single block
	fmt.Printf("[DEBUG] Block %d: Creating BlockBatch...\n", blockNumber)
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
			fmt.Printf("[BLOCK] Block %d: First operation is CREATE with key: %s, contentType: %s\n",
				blockNumber, firstOp.Create.Key.Hex(), firstOp.Create.ContentType)
		}
	}

	// Push the batch to the shared iterator - FollowEvents will process it
	pushIterator.Push(ctx, blockBatch)
	fmt.Printf("[BLOCK] Block %d: Block batch pushed to iterator, FollowEvents will process it\n", blockNumber)

	// // Update block number and verify in background (non-blocking)
	// go func(blockNum int64) {
	// 	// Give FollowEvents a moment to process
	// 	time.Sleep(100 * time.Millisecond)

	// 	// Update block number in store after processing
	// 	if err := updateBlockNumberInStore(context.Background(), blockNum); err != nil {
	// 		fmt.Printf("[BLOCK] Block %d: Warning - Failed to update block number in store: %v\n", blockNum, err)
	// 	} else {
	// 		fmt.Printf("[BLOCK] Block %d: Block number updated in store to %d\n", blockNum, blockNum)
	// 	}

	// 	// Verify entities were saved by checking count
	// 	verifyCount, err := CountEntities()
	// 	if err != nil {
	// 		fmt.Printf("[BLOCK] Block %d: Warning - Could not verify entity count: %v\n", blockNum, err)
	// 	} else {
	// 		fmt.Printf("[BLOCK] Block %d: Database now contains %d total entities\n", blockNum, verifyCount)
	// 	}
	// }(blockNumber)

	insertDuration := time.Since(insertStartTime)

	totalDuration := time.Since(totalStartTime)
	message := fmt.Sprintf("[BLOCK] Block %d processed: %d entities - %.2fms", blockNumber, len(pendingEntities), totalDuration.Seconds()*1000)
	fmt.Println(message)

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

// updateBlockNumberInStore updates the block number in the store after processing
func updateBlockNumberInStore(ctx context.Context, blockNumber int64) error {
	storeMutex.RLock()
	s := storeInstance
	storeMutex.RUnlock()

	if s == nil {
		return fmt.Errorf("store not initialized")
	}

	queries := s.NewQueries()
	return queries.UpsertLastBlock(ctx, uint64(blockNumber))
}
