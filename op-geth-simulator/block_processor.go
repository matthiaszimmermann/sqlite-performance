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
		timestamp := time.Now().Format(time.RFC3339)
		fmt.Printf("[%s] [DEBUG] [BLOCK] Starting FollowEvents goroutine...\n", timestamp)
		batchIterator := pushIterator.Iterator()
		if err := FollowEvents(followEventsCtx, arkivevents.BatchIterator(batchIterator)); err != nil {
			if err != context.Canceled {
				timestamp := time.Now().Format(time.RFC3339)
				fmt.Printf("[%s] [ERROR] [BLOCK] FollowEvents error: %v\n", timestamp, err)
			} else {
				timestamp := time.Now().Format(time.RFC3339)
				fmt.Printf("[%s] [DEBUG] [BLOCK] FollowEvents stopped (context canceled)\n", timestamp)
			}
		} else {
			timestamp := time.Now().Format(time.RFC3339)
			fmt.Printf("[%s] [DEBUG] [BLOCK] FollowEvents exited normally\n", timestamp)
		}
	}()

	// Create ticker for 2 second intervals
	intervalID = time.NewTicker(2 * time.Second)

	go func() {
		tickCount := 0
		for range intervalID.C {
			tickCount++
			timestamp := time.Now().Format(time.RFC3339)
			queueSize := writeQueue.GetQueueSize()
			fmt.Printf("[%s] [DEBUG] [BLOCK] Block processor tick #%d - Queue size: %d\n", timestamp, tickCount, queueSize)

			// Wrap processBlock in a recover to prevent crashes from stopping the ticker
			func() {
				defer func() {
					if r := recover(); r != nil {
						timestamp := time.Now().Format(time.RFC3339)
						fmt.Printf("[%s] [ERROR] [BLOCK] Panic in processBlock: %v\n", timestamp, r)
					}
				}()
				processStartTime := time.Now()
				processBlock()
				processDuration := time.Since(processStartTime)
				timestamp := time.Now().Format(time.RFC3339)
				fmt.Printf("[%s] [DEBUG] [BLOCK] processBlock() completed in %v\n", timestamp, processDuration)
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
	pendingCreates, pendingUpdates := writeQueue.DequeueAll()

	// Create a single block for all events in this block number
	block := events.Block{
		Number:     uint64(blockNumber),
		Operations: []events.Operation{},
	}
	ctx := context.Background()

	if len(pendingCreates) == 0 && len(pendingUpdates) == 0 {
		logBlockDebug(blockNumber, "No pending entities to process")
		return
	}

	// Count attributes for pending entities
	stringCount := 0
	numericCount := 0
	stringCreates, numericCreates := countAttributes(pendingCreates)
	stringUpdates, numericUpdates := countAttributes(pendingUpdates)
	stringCount = stringCreates + stringUpdates
	numericCount = numericCreates + numericUpdates

	totalPending := len(pendingCreates) + len(pendingUpdates)
	logBlockInfoMsg(blockNumber, "Processing %d entities (%d creates, %d updates)", totalPending, len(pendingCreates), len(pendingUpdates))
	logBlockDebug(blockNumber, "Starting to build events...")
	logBlockDebug(blockNumber, "Attributes counted - string: %d, numeric: %d", stringCount, numericCount)

	// Create CREATE events first
	logBlockDebug(blockNumber, "Creating CREATE events for %d entities...", len(pendingCreates))
	for i, pendingEntity := range pendingCreates {
		entity := &pendingEntity.Entity
		entity.CreatedAtBlock = blockNumber
		entity.LastModifiedAtBlock = blockNumber

		// Create CREATE event for the entity
		entityKey := []byte(entity.Key)
		var payload []byte
		if len(entity.Payload) > 0 {
			payload = entity.Payload
		}

		// Convert entity key to Hash (32 bytes)
		keyHash := sha256.Sum256(entityKey)
		keyHashHex := common.Hash(keyHash).Hex()

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
		opNum := i
		txIndex := uint64(opNum / 10)
		opIndex := uint64(opNum % 10)

		// Calculate BTL
		btl := uint64(entity.ExpiresAt - entity.LastModifiedAtBlock)

		// Log detailed entity content for debugging
		logBlockDebug(blockNumber, "Entity %d/%d: key=%s, payloadSize=%d, contentType=%s, owner=%s, btl=%d, txIndex=%d, opIndex=%d",
			i+1, len(pendingCreates), keyHashHex, len(payload), entity.ContentType, ownerAddr.Hex(), btl, txIndex, opIndex)

		// Log string attributes
		if len(stringAttrs) > 0 {
			attrsStr := ""
			first := true
			for k, v := range stringAttrs {
				if !first {
					attrsStr += ", "
				}
				attrsStr += fmt.Sprintf("%s=%s", k, v)
				first = false
			}
			logBlockDebug(blockNumber, "Entity %d/%d string attributes: %s", i+1, len(pendingCreates), attrsStr)
		}

		// Log numeric attributes
		if len(numericAttrs) > 0 {
			attrsStr := ""
			first := true
			for k, v := range numericAttrs {
				if !first {
					attrsStr += ", "
				}
				attrsStr += fmt.Sprintf("%s=%d", k, v)
				first = false
			}
			logBlockDebug(blockNumber, "Entity %d/%d numeric attributes: %s", i+1, len(pendingCreates), attrsStr)
		}

		// Log payload preview (first 100 bytes if available)
		if len(payload) > 0 {
			previewLen := 100
			if len(payload) < previewLen {
				previewLen = len(payload)
			}
			// Show first few bytes as hex
			previewHex := fmt.Sprintf("%x", payload[:previewLen])
			if len(payload) > previewLen {
				logBlockDebug(blockNumber, "Entity %d/%d payload preview (first %d/%d bytes): %s...", i+1, len(pendingCreates), previewLen, len(payload), previewHex)
			} else {
				logBlockDebug(blockNumber, "Entity %d/%d payload (%d bytes): %s", i+1, len(pendingCreates), len(payload), previewHex)
			}
		} else {
			logBlockDebug(blockNumber, "Entity %d/%d has empty payload", i+1, len(pendingCreates))
		}

		// Add create operation to the block
		createOp := events.Operation{
			TxIndex: txIndex,
			OpIndex: opIndex,
			Create: &events.OPCreate{
				Key:               common.Hash(keyHash),
				ContentType:       entity.ContentType,
				BTL:               btl,
				Owner:             ownerAddr,
				Content:           payload,
				StringAttributes:  stringAttrs,
				NumericAttributes: numericAttrs,
			},
		}
		block.Operations = append(block.Operations, createOp)
	}

	// Then add UPDATE events (at the end, after creates)
	logBlockDebug(blockNumber, "Creating UPDATE events for %d entities...", len(pendingUpdates))
	for j, pendingEntity := range pendingUpdates {
		entity := &pendingEntity.Entity
		entity.CreatedAtBlock = blockNumber
		entity.LastModifiedAtBlock = blockNumber

		entityKey := []byte(entity.Key)
		var payload []byte
		if len(entity.Payload) > 0 {
			payload = entity.Payload
		}

		keyHash := sha256.Sum256(entityKey)
		keyHashHex := common.Hash(keyHash).Hex()

		var ownerAddr common.Address
		if entity.OwnerAddress != "" {
			ownerAddr = common.HexToAddress(entity.OwnerAddress)
		}

		stringAttrs := make(map[string]string)
		if entity.StringAnnotations != nil {
			stringAttrs = entity.StringAnnotations
		}

		numericAttrs := make(map[string]uint64)
		if entity.NumericAnnotations != nil {
			for k, v := range entity.NumericAnnotations {
				numericAttrs[k] = uint64(v)
			}
		}

		opNum := len(pendingCreates) + j
		txIndex := uint64(opNum / 10)
		opIndex := uint64(opNum % 10)

		btl := uint64(entity.ExpiresAt - entity.LastModifiedAtBlock)

		logBlockDebug(blockNumber, "UPDATE %d/%d: key=%s, payloadSize=%d, contentType=%s, owner=%s, btl=%d, txIndex=%d, opIndex=%d",
			j+1, len(pendingUpdates), keyHashHex, len(payload), entity.ContentType, ownerAddr.Hex(), btl, txIndex, opIndex)

		updateOp := events.Operation{
			TxIndex: txIndex,
			OpIndex: opIndex,
			Update: &events.OPUpdate{
				Key:               common.Hash(keyHash),
				ContentType:       entity.ContentType,
				BTL:               btl,
				Owner:             ownerAddr,
				Content:           payload,
				StringAttributes:  stringAttrs,
				NumericAttributes: numericAttrs,
			},
		}
		block.Operations = append(block.Operations, updateOp)
	}

	// Get expired entity key hashes and create delete operations
	logBlockDebug(blockNumber, "Querying for expired entities (expiration <= %d)...", blockNumber)
	expiredEntityKeyHashes, err := GetExpiredEntities(blockNumber)
	if err != nil {
		logBlockDebug(blockNumber, "Error querying expired entities: %v", err)
	} else {
		logBlockInfoMsg(blockNumber, "Found %d expired entities to delete", len(expiredEntityKeyHashes))

		// Start operation index after all create + update operations
		startOpIndex := len(block.Operations)

		for i, keyHash := range expiredEntityKeyHashes {
			keyHashHex := keyHash.Hex()

			logBlockDebug(blockNumber, "Expired entity %d/%d: key=%s", i+1, len(expiredEntityKeyHashes), keyHashHex)

			// Calculate transaction and operation indices (10 operations per transaction)
			// Continue from where create operations left off
			opIndex := startOpIndex + i
			txIndex := uint64(opIndex / 10)
			opIndexInTx := uint64(opIndex % 10)

			// Create delete operation
			// OPDelete is a type alias for common.Hash
			deleteOp := events.Operation{
				TxIndex: txIndex,
				OpIndex: opIndexInTx,
				Delete:  (*events.OPDelete)(&keyHash),
			}
			block.Operations = append(block.Operations, deleteOp)
		}

		if len(expiredEntityKeyHashes) > 0 {
			logBlockInfoMsg(blockNumber, "Created %d delete operations for expired entities", len(expiredEntityKeyHashes))
		}
	}

	// Log summary of all entities in the block
	totalPayloadSize := 0
	totalStringAttrs := 0
	totalNumericAttrs := 0
	createCount := 0
	updateCount := 0
	deleteCount := 0
	for _, op := range block.Operations {
		if op.Create != nil {
			totalPayloadSize += len(op.Create.Content)
			totalStringAttrs += len(op.Create.StringAttributes)
			totalNumericAttrs += len(op.Create.NumericAttributes)
			createCount++
		}
		if op.Update != nil {
			totalPayloadSize += len(op.Update.Content)
			totalStringAttrs += len(op.Update.StringAttributes)
			totalNumericAttrs += len(op.Update.NumericAttributes)
			updateCount++
		}
		if op.Delete != nil {
			deleteCount++
		}
	}
	logBlockDebug(blockNumber, "Created %d total operations (%d creates, %d updates, %d deletes)",
		len(block.Operations), createCount, updateCount, deleteCount)
	logBlockDebug(blockNumber, "Block summary: totalPayloadSize=%d bytes, totalStringAttrs=%d, totalNumericAttrs=%d",
		totalPayloadSize, totalStringAttrs, totalNumericAttrs)

	// Only push block if there are operations (creates or deletes)
	if len(block.Operations) == 0 {
		logBlockDebug(blockNumber, "No operations to process, skipping block push")
		return
	}

	// Use pusher to create block event batches
	// Create BlockBatch with the single block
	logBlockDebug(blockNumber, "Creating BlockBatch...")
	blockBatch := events.BlockBatch{
		Blocks: []events.Block{block},
	}

	// Push the block batch to the shared PushIterator
	// FollowEvents (running in background) will pick it up automatically
	logBlockInfoMsg(blockNumber, "Pushing block batch with %d operations to iterator (block number: %d)",
		len(block.Operations), block.Number)

	// Log first operation details for debugging
	if len(block.Operations) > 0 {
		firstOp := block.Operations[0]
		if firstOp.Create != nil {
			logBlockDebug(blockNumber, "First operation is CREATE with key: %s, contentType: %s",
				firstOp.Create.Key.Hex(), firstOp.Create.ContentType)
		}
	}

	// Push the batch to the shared iterator - FollowEvents will process it
	// Note: Push() may block if the iterator buffer is full, but it should not block indefinitely
	logBlockDebug(blockNumber, "Calling pushIterator.Push()...")
	pushStartTime := time.Now()

	// Use a goroutine with timeout to detect if Push() is blocking
	pushDone := make(chan bool, 1)
	var pushErr error
	go func() {
		defer func() {
			if r := recover(); r != nil {
				timestamp := time.Now().Format(time.RFC3339)
				fmt.Printf("[%s] [ERROR] [BLOCK] Panic in pushIterator.Push(): %v\n", timestamp, r)
				pushErr = fmt.Errorf("panic: %v", r)
				pushDone <- true
			}
		}()
		pushIterator.Push(ctx, blockBatch)
		pushDone <- true
	}()

	// Wait for push with timeout
	select {
	case <-pushDone:
		pushDuration := time.Since(pushStartTime)
		if pushErr != nil {
			logBlockDebug(blockNumber, "pushIterator.Push() failed: %v", pushErr)
		} else {
			logBlockDebug(blockNumber, "pushIterator.Push() completed in %v", pushDuration)
		}
	case <-time.After(5 * time.Second):
		timestamp := time.Now().Format(time.RFC3339)
		fmt.Printf("[%s] [ERROR] [BLOCK] pushIterator.Push() blocked for more than 5 seconds! This may indicate FollowEvents is not consuming batches.\n", timestamp)
		logBlockDebug(blockNumber, "pushIterator.Push() timeout - FollowEvents may be stuck")
	}

	logBlockInfoMsg(blockNumber, "Block batch pushed to iterator, FollowEvents will process it")

	totalDuration := time.Since(totalStartTime)
	logBlockInfoMsg(blockNumber, "Processed %d operations (%d creates, %d updates, %d deletes) - %.2fms",
		len(block.Operations), createCount, updateCount, deleteCount, totalDuration.Seconds()*1000)

	// Log to processing.log
	logToProcessingLog(
		fmt.Sprintf("%s BLOCK %d %d %d %d %d",
			testName,
			blockNumber,
			int(totalDuration.Milliseconds()),
			totalPending,
			stringCount,
			numericCount,
		),
	)

	// Warn if block processing takes more than 1000ms
	if totalDuration > 1000*time.Millisecond {
		logBlockWarning(blockNumber, len(block.Operations), totalDuration)
	}
}
