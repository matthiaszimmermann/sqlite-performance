package main

import (
	"context"
	cryptorand "crypto/rand"
	"crypto/sha256"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"time"

	arkivevents "github.com/Arkiv-Network/arkiv-events"
	"github.com/Arkiv-Network/arkiv-events/events"
	pebblestore "github.com/Arkiv-Network/pebble-bitmap-store/pebblestore"
	"github.com/Arkiv-Network/pebble-bitmap-store/pusher"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
)

const (
	blockPoolSize = 5000 // Number of blocks to keep in memory
	batchSize     = 100  // Number of blocks to write in each batch
	csvLogFile    = "replication_log.csv"
)

type BlockData struct {
	Payloads []PayloadData
}

type PayloadData struct {
	EntityKey          []byte
	Payload            []byte
	ContentType        string
	StringAttributes   map[string]string
	NumericAttributes  map[string]uint64
	OwnerAddressString string
}

var (
	blockPool                []BlockData
	targetStore              *pebblestore.PebbleStore
	targetPushIterator       *pusher.PushIterator
	targetFollowEventsCtx    context.Context
	targetFollowEventsCancel context.CancelFunc
	totalBlocksReplicated    int
	totalPayloads            int
	totalStringAttrs         int
	totalNumericAttrs        int
	writeTimes               []float64
	targetFollowEventsWG     sync.WaitGroup
)

// generateNewEntityKey generates a new 32-byte entity key
func generateNewEntityKey() []byte {
	key := make([]byte, 32)
	cryptorand.Read(key)
	return key
}

// readAllSourcePayloads loads entities from a source Pebble store using pagination.
func readAllSourcePayloads(sourceStore *pebblestore.PebbleStore) ([]PayloadData, error) {
	ctx := context.Background()

	lastBlock, err := sourceStore.GetLastBlock(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get source last block: %w", err)
	}

	atBlock := hexutil.Uint64(lastBlock)
	resultsPerPage := hexutil.Uint64(200)
	includeData := &pebblestore.IncludeData{
		Key:                         true,
		Attributes:                  true,
		SyntheticAttributes:         false,
		Payload:                     true,
		ContentType:                 true,
		Expiration:                  false,
		Creator:                     false,
		Owner:                       true,
		CreatedAtBlock:              false,
		LastModifiedAtBlock:         false,
		TransactionIndexInBlock:     false,
		OperationIndexInTransaction: false,
	}

	payloads := make([]PayloadData, 0, 10000)
	cursor := ""

	for {
		options := &pebblestore.Options{
			AtBlock:        &atBlock,
			ResultsPerPage: &resultsPerPage,
			IncludeData:    includeData,
			Cursor:         cursor,
		}

		response, err := sourceStore.QueryEntities(ctx, "$all", options)
		if err != nil {
			return nil, fmt.Errorf("failed to query source entities: %w", err)
		}

		for _, item := range response.Data {
			payload, err := parseSourcePayload(item)
			if err != nil {
				continue
			}
			payloads = append(payloads, payload)
		}

		if response.Cursor == nil || *response.Cursor == "" {
			break
		}
		cursor = *response.Cursor
	}

	return payloads, nil
}

func parseSourcePayload(data json.RawMessage) (PayloadData, error) {
	type sourceEntityData struct {
		Key              *common.Hash    `json:"key,omitempty"`
		Value            hexutil.Bytes   `json:"value,omitempty"`
		ContentType      *string         `json:"contentType,omitempty"`
		Owner            *common.Address `json:"owner,omitempty"`
		StringAttributes []struct {
			Key   string `json:"key"`
			Value string `json:"value"`
		} `json:"stringAttributes,omitempty"`
		NumericAttributes []struct {
			Key   string `json:"key"`
			Value uint64 `json:"value"`
		} `json:"numericAttributes,omitempty"`
	}

	var parsed sourceEntityData
	if err := json.Unmarshal(data, &parsed); err != nil {
		return PayloadData{}, err
	}
	if parsed.Key == nil {
		return PayloadData{}, fmt.Errorf("missing key")
	}

	payload := PayloadData{
		EntityKey:         parsed.Key.Bytes(),
		Payload:           []byte(parsed.Value),
		StringAttributes:  make(map[string]string),
		NumericAttributes: make(map[string]uint64),
	}
	if parsed.ContentType != nil {
		payload.ContentType = *parsed.ContentType
	}
	if parsed.Owner != nil {
		payload.OwnerAddressString = parsed.Owner.Hex()
	}

	for _, attr := range parsed.StringAttributes {
		payload.StringAttributes[attr.Key] = attr.Value
	}
	for _, attr := range parsed.NumericAttributes {
		payload.NumericAttributes[attr.Key] = attr.Value
	}

	if payload.OwnerAddressString != "" {
		payload.StringAttributes["ownerAddress"] = payload.OwnerAddressString
	}

	return payload, nil
}

// createEntityKeyMap creates a mapping from old entity keys to new entity keys
func createEntityKeyMap(blockData *BlockData) map[string][]byte {
	keyMap := make(map[string][]byte)
	seenKeys := make(map[string]bool)

	// Collect all unique entity keys from the block
	for _, payload := range blockData.Payloads {
		keyStr := fmt.Sprintf("%x", payload.EntityKey)
		if !seenKeys[keyStr] {
			seenKeys[keyStr] = true
			keyMap[keyStr] = generateNewEntityKey()
		}
	}

	return keyMap
}

// loadBlockPool loads a pool of random entity groups into memory
// Each "block" in the pool is a group of entities (simulating a block)
func loadBlockPool(sourceStore *pebblestore.PebbleStore) error {
	fmt.Println("Loading entity pool into memory...")
	sourcePayloads, err := readAllSourcePayloads(sourceStore)
	if err != nil {
		return fmt.Errorf("failed to read source entities: %w", err)
	}

	fmt.Printf("Found %d entities in source store\n", len(sourcePayloads))

	if len(sourcePayloads) == 0 {
		return fmt.Errorf("no entities found in source store")
	}

	// Randomly select entity keys to form blocks
	// Each "block" will contain a random group of entities
	entitiesPerBlock := 100 // Approximate entities per block
	totalEntitiesToLoad := blockPoolSize * entitiesPerBlock
	if len(sourcePayloads) < totalEntitiesToLoad {
		totalEntitiesToLoad = len(sourcePayloads)
	}

	// Shuffle entities
	rand.Shuffle(len(sourcePayloads), func(i, j int) {
		sourcePayloads[i], sourcePayloads[j] = sourcePayloads[j], sourcePayloads[i]
	})
	selectedPayloads := sourcePayloads[:totalEntitiesToLoad]

	fmt.Printf("Loading %d entities into memory (forming ~%d blocks)...\n", totalEntitiesToLoad, blockPoolSize)
	loadStartTime := time.Now()

	// Group entities into blocks.
	blockPool = make([]BlockData, 0, blockPoolSize)
	for i := 0; i < len(selectedPayloads); i += entitiesPerBlock {
		end := i + entitiesPerBlock
		if end > len(selectedPayloads) {
			end = len(selectedPayloads)
		}

		blockData := BlockData{Payloads: selectedPayloads[i:end]}
		if len(blockData.Payloads) > 0 {
			blockPool = append(blockPool, blockData)
		}
	}

	loadDuration := time.Since(loadStartTime)
	fmt.Printf("Block pool loaded: %d blocks in memory (%.2fms)\n", len(blockPool), float64(loadDuration.Nanoseconds())/1e6)

	return nil
}

// initializeTargetDatabase initializes the target database and starts FollowEvents
func initializeTargetDatabase(targetDbPath string) error {
	fmt.Println("Opening target database...")
	logger := GetStoreLogger()
	store, err := pebblestore.NewPebbleStore(logger, targetDbPath)
	if err != nil {
		return fmt.Errorf("failed to initialize target store: %w", err)
	}
	targetStore = store

	// Create shared PushIterator for all blocks
	targetPushIterator = pusher.NewPushIterator()

	// Create context for FollowEvents
	targetFollowEventsCtx, targetFollowEventsCancel = context.WithCancel(context.Background())

	// Start FollowEvents in a separate goroutine - it will run continuously
	targetFollowEventsWG.Add(1)
	go func() {
		defer targetFollowEventsWG.Done()
		fmt.Println("[FOLLOW] Starting FollowEvents goroutine for replication...")
		batchIterator := targetPushIterator.Iterator()
		if err := store.FollowEvents(targetFollowEventsCtx, arkivevents.BatchIterator(batchIterator)); err != nil {
			if err != context.Canceled {
				fmt.Printf("[FOLLOW] FollowEvents error: %v\n", err)
			} else {
				fmt.Println("[FOLLOW] FollowEvents stopped (context canceled)")
			}
		}
	}()

	return nil
}

// writeReplicatedBlockBatch writes a batch of replicated blocks to target database
func writeReplicatedBlockBatch(blocksData []BlockData, targetBlockNumber int64) error {
	writeStartTime := time.Now()

	// Create a single block for all events
	block := events.Block{
		Number:     uint64(targetBlockNumber),
		Operations: []events.Operation{},
	}

	// Process all blocks in the batch
	for _, blockData := range blocksData {
		entityKeyMap := createEntityKeyMap(&blockData)

		// Process payloads
		for i, payload := range blockData.Payloads {
			oldKeyStr := fmt.Sprintf("%x", payload.EntityKey)
			newEntityKey := entityKeyMap[oldKeyStr]
			if newEntityKey == nil {
				newEntityKey = generateNewEntityKey()
			}

			stringAttrs := make(map[string]string, len(payload.StringAttributes))
			for k, v := range payload.StringAttributes {
				stringAttrs[k] = v
			}

			numericAttrsUint64 := make(map[string]uint64, len(payload.NumericAttributes))
			for k, v := range payload.NumericAttributes {
				numericAttrsUint64[k] = v
			}

			// Calculate transaction and operation indices (10 operations per transaction)
			txIndex := uint64(i / 10)
			opIndex := uint64(i % 10)

			// Create create operation
			keyHash := sha256.Sum256(newEntityKey)
			// BTL (Block Time to Live) - set a default expiration (e.g., 7 days in blocks)
			// Assuming ~2 second blocks, 7 days = 7 * 24 * 3600 / 2 = 302400 blocks
			defaultBTL := uint64(302400)
			createOp := events.Operation{
				TxIndex: txIndex,
				OpIndex: opIndex,
				Create: &events.OPCreate{
					Key:               common.Hash(keyHash),
					ContentType:       payload.ContentType,
					BTL:               defaultBTL,
					Owner:             common.Address{}, // Will be extracted from attributes if present
					Content:           payload.Payload,
					StringAttributes:  stringAttrs,
					NumericAttributes: numericAttrsUint64,
				},
			}

			// Extract owner from attributes if present.
			if ownerAddr, ok := stringAttrs["ownerAddress"]; ok {
				createOp.Create.Owner = common.HexToAddress(ownerAddr)
			}

			block.Operations = append(block.Operations, createOp)
		}
	}

	// Create BlockBatch and push to iterator
	blockBatch := events.BlockBatch{
		Blocks: []events.Block{block},
	}

	targetPushIterator.Push(targetFollowEventsCtx, blockBatch)

	writeDuration := time.Since(writeStartTime)
	writeTimes = append(writeTimes, float64(writeDuration.Nanoseconds())/1e6)

	return nil
}

// initializeCsvLog initializes the CSV log file
func initializeCsvLog() error {
	file, err := os.Create(csvLogFile)
	if err != nil {
		return fmt.Errorf("failed to create CSV log file: %w", err)
	}
	defer file.Close()

	writer := csv.NewWriter(file)
	defer writer.Flush()

	header := []string{"num_payloads", "num_string_attributes", "num_numeric_attributes", "read_time_ms", "write_time_ms", "output_db_size_bytes"}
	return writer.Write(header)
}

// writeCsvRow writes a row to the CSV log file
func writeCsvRow(numPayloads, numStringAttrs, numNumericAttrs int, readTimeMs, writeTimeMs float64, outputDbSizeBytes int64) error {
	file, err := os.OpenFile(csvLogFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer file.Close()

	writer := csv.NewWriter(file)
	defer writer.Flush()

	row := []string{
		fmt.Sprintf("%d", numPayloads),
		fmt.Sprintf("%d", numStringAttrs),
		fmt.Sprintf("%d", numNumericAttrs),
		fmt.Sprintf("%.2f", readTimeMs),
		fmt.Sprintf("%.2f", writeTimeMs),
		fmt.Sprintf("%d", outputDbSizeBytes),
	}
	return writer.Write(row)
}

// getOutputDbSize gets the size of the output database file
func getOutputDbSize(targetDbPath string) int64 {
	var total int64
	err := filepath.Walk(targetDbPath, func(_ string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if !info.IsDir() {
			total += info.Size()
		}
		return nil
	})
	if err != nil {
		return 0
	}
	return total
}

// processBatch processes a batch of blocks
func processBatch(batchSize int, targetBlockNumber int64) (int, int, int, float64, error) {
	if len(blockPool) == 0 {
		return 0, 0, 0, 0, fmt.Errorf("block pool is empty")
	}

	batchStartTime := time.Now()

	// Select random blocks from the pool to replicate
	blocksToReplicate := make([]BlockData, 0, batchSize)
	for i := 0; i < batchSize && i < len(blockPool); i++ {
		randomIndex := rand.Intn(len(blockPool))
		blocksToReplicate = append(blocksToReplicate, blockPool[randomIndex])
	}

	// Calculate totals for logging.
	batchPayloads := 0
	batchStringAttrs := 0
	batchNumericAttrs := 0

	for _, blockData := range blocksToReplicate {
		batchPayloads += len(blockData.Payloads)

		for _, payload := range blockData.Payloads {
			batchStringAttrs += len(payload.StringAttributes)
			batchNumericAttrs += len(payload.NumericAttributes)
		}
	}

	// Write the batch
	if err := writeReplicatedBlockBatch(blocksToReplicate, targetBlockNumber); err != nil {
		return 0, 0, 0, 0, fmt.Errorf("failed to write batch: %w", err)
	}

	batchDuration := time.Since(batchStartTime)

	return batchPayloads, batchStringAttrs, batchNumericAttrs, float64(batchDuration.Nanoseconds()) / 1e6, nil
}

// printFinalStatistics prints final replication statistics
func printFinalStatistics() {
	if totalBlocksReplicated > 0 {
		fmt.Println("\n\n=== Replication Statistics ===")
		fmt.Printf("Total blocks replicated: %d\n", totalBlocksReplicated)
		fmt.Printf("Total payloads: %d\n", totalPayloads)
		fmt.Printf("Total string attributes: %d\n", totalStringAttrs)
		fmt.Printf("Total numeric attributes: %d\n", totalNumericAttrs)

		if len(writeTimes) > 0 {
			var sum float64
			for _, t := range writeTimes {
				sum += t
			}
			avgWriteTime := sum / float64(len(writeTimes))
			fmt.Println("\n=== Average Times ===")
			fmt.Printf("Write time: %.2fms\n", avgWriteTime)

			// Calculate percentiles
			sortedTimes := make([]float64, len(writeTimes))
			copy(sortedTimes, writeTimes)
			sort.Float64s(sortedTimes)

			writeP50 := sortedTimes[len(sortedTimes)*50/100]
			writeP95 := sortedTimes[len(sortedTimes)*95/100]
			writeP99 := sortedTimes[len(sortedTimes)*99/100]

			fmt.Println("\n=== Write Performance Percentiles ===")
			fmt.Printf("P50 (median): %.2fms\n", writeP50)
			fmt.Printf("P95: %.2fms\n", writeP95)
			fmt.Printf("P99: %.2fms\n", writeP99)
			fmt.Printf("Min: %.2fms\n", sortedTimes[0])
			fmt.Printf("Max: %.2fms\n", sortedTimes[len(sortedTimes)-1])
		}
	}
}

// RunBlockReplicatorCLI runs the block replicator from command line
func RunBlockReplicatorCLI() {
	args := os.Args[2:] // Skip "replicate" command

	if len(args) < 2 {
		fmt.Println("Usage: go run . replicate <source_db> <target_db> [num_blocks]")
		fmt.Println("Example: go run . replicate mendoza.db output.db 1000")
		fmt.Println("         go run . replicate mendoza.db output.db (replicates all available blocks)")
		os.Exit(1)
	}

	sourceDbPath := args[0]
	targetDbPath := args[1]

	numBlocks := 0 // 0 means replicate all available blocks
	if len(args) >= 3 {
		var err error
		numBlocks, err = strconv.Atoi(args[2])
		if err != nil || numBlocks <= 0 {
			fmt.Printf("Error: Number of blocks must be a positive number, got: %s\n", args[2])
			os.Exit(1)
		}
	}

	// If numBlocks is 0, set to a very large number to replicate all blocks
	if numBlocks == 0 {
		numBlocks = 999999999
	}

	if err := RunBlockReplicator(sourceDbPath, targetDbPath, numBlocks); err != nil {
		log.Fatalf("Error: %v", err)
	}
}

// RunBlockReplicator runs the block replicator
func RunBlockReplicator(sourceDbPath, targetDbPath string, numBlocks int) error {
	// Seed random number generator
	rand.Seed(time.Now().UnixNano())

	fmt.Println("Opening source Pebble store...")
	logger := GetStoreLogger()
	sourceStore, err := pebblestore.NewPebbleStore(logger, sourceDbPath)
	if err != nil {
		return fmt.Errorf("failed to open source store: %w", err)
	}
	defer sourceStore.Close()

	// Load block pool into memory
	if err := loadBlockPool(sourceStore); err != nil {
		return err
	}

	// Initialize target database
	if err := initializeTargetDatabase(targetDbPath); err != nil {
		return fmt.Errorf("failed to initialize target database: %w", err)
	}
	defer func() {
		if targetPushIterator != nil {
			targetPushIterator.Close()
		}
		if targetFollowEventsCancel != nil {
			targetFollowEventsCancel()
		}
		targetFollowEventsWG.Wait()
		if targetStore != nil {
			targetStore.Close()
		}
	}()

	// Initialize CSV log file
	fmt.Printf("Initializing CSV log file: %s\n", csvLogFile)
	if err := initializeCsvLog(); err != nil {
		return fmt.Errorf("failed to initialize CSV log: %w", err)
	}

	fmt.Printf("Starting block replicator (processing batches of %d blocks, target: %d blocks)...\n", batchSize, numBlocks)

	startTime := time.Now()
	targetBlockNumber := int64(1)

	// Continuously process batches until we reach the target number of blocks
	for totalBlocksReplicated < numBlocks {
		remaining := numBlocks - totalBlocksReplicated
		currentBatchSize := batchSize
		if remaining < batchSize {
			currentBatchSize = remaining
		}

		// Process batch
		batchPayloads, batchStringAttrs, batchNumericAttrs, batchDuration, err := processBatch(currentBatchSize, targetBlockNumber)
		if err != nil {
			return fmt.Errorf("failed to process batch: %w", err)
		}

		totalBlocksReplicated += currentBatchSize
		totalPayloads += batchPayloads
		totalStringAttrs += batchStringAttrs
		totalNumericAttrs += batchNumericAttrs
		targetBlockNumber++

		// Write CSV log entry
		outputDbSize := getOutputDbSize(targetDbPath)
		if err := writeCsvRow(batchPayloads, batchStringAttrs, batchNumericAttrs, 0, batchDuration, outputDbSize); err != nil {
			fmt.Printf("Warning: Failed to write CSV row: %v\n", err)
		}

		message := fmt.Sprintf("[BATCH] Processed %d blocks: %d payloads, %d str attrs, %d num attrs - %.2fms",
			currentBatchSize, batchPayloads, batchStringAttrs, batchNumericAttrs, batchDuration)
		fmt.Println(message)

		// Warn if batch processing takes more than 1000ms
		if batchDuration > 1000 {
			fmt.Printf("⚠️  WARNING: Batch processing took %.2fms\n", batchDuration)
		}
	}

	totalTime := time.Since(startTime).Seconds()
	fmt.Printf("\nTotal time: %.2fs\n", totalTime)
	fmt.Printf("Blocks per second: %.2f\n", float64(totalBlocksReplicated)/totalTime)

	printFinalStatistics()

	return nil
}
