package main

import (
	"context"
	cryptorand "crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"os"
	"sort"
	"strconv"
	"time"

	arkivevents "github.com/Arkiv-Network/arkiv-events"
	"github.com/Arkiv-Network/arkiv-events/events"
	sqlitestore "github.com/Arkiv-Network/sqlite-bitmap-store"
	"github.com/Arkiv-Network/sqlite-bitmap-store/pusher"
	"github.com/ethereum/go-ethereum/common"
	_ "github.com/mattn/go-sqlite3"
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
	EntityKey         []byte
	Payload           []byte
	ContentType       string
	StringAttributes  string // JSON string
	NumericAttributes string // JSON string
}

var (
	blockPool                []BlockData
	targetStore              *sqlitestore.SQLiteStore
	targetPushIterator       *pusher.PushIterator
	targetFollowEventsCtx    context.Context
	targetFollowEventsCancel context.CancelFunc
	totalBlocksReplicated    int
	totalPayloads            int
	totalStringAttrs         int
	totalNumericAttrs        int
	writeTimes               []float64
)

// generateNewEntityKey generates a new 32-byte entity key
func generateNewEntityKey() []byte {
	key := make([]byte, 32)
	cryptorand.Read(key)
	return key
}

// getAvailableBlocks gets all available entity keys from source database
// Since the new schema doesn't have from_block, we'll group by entity_key
func getAvailableEntityKeys(sourceDb *sql.DB) ([][]byte, error) {
	query := `
		SELECT DISTINCT entity_key 
		FROM payloads 
		ORDER BY entity_key
		LIMIT 10000
	`
	rows, err := sourceDb.Query(query)
	if err != nil {
		return nil, fmt.Errorf("failed to query available entity keys: %w", err)
	}
	defer rows.Close()

	var keys [][]byte
	for rows.Next() {
		var key []byte
		if err := rows.Scan(&key); err != nil {
			return nil, fmt.Errorf("failed to scan entity key: %w", err)
		}
		keys = append(keys, key)
	}
	return keys, rows.Err()
}

// readEntityData reads data for specific entity keys from source database
func readEntityData(sourceDb *sql.DB, entityKeys [][]byte) (*BlockData, error) {
	blockData := &BlockData{}

	// Read payloads for the given entity keys
	// Use IN clause or prepare statement for multiple keys
	if len(entityKeys) == 0 {
		return blockData, nil
	}

	// Build query with placeholders
	placeholders := ""
	args := make([]interface{}, len(entityKeys))
	for i, key := range entityKeys {
		if i > 0 {
			placeholders += ","
		}
		placeholders += "?"
		args[i] = key
	}

	payloadsQuery := fmt.Sprintf(`
		SELECT entity_key, payload, content_type, string_attributes, numeric_attributes
		FROM payloads
		WHERE entity_key IN (%s)
	`, placeholders)

	rows, err := sourceDb.Query(payloadsQuery, args...)
	if err != nil {
		return nil, fmt.Errorf("failed to query payloads: %w", err)
	}
	defer rows.Close()

	for rows.Next() {
		var payload PayloadData
		err := rows.Scan(
			&payload.EntityKey,
			&payload.Payload,
			&payload.ContentType,
			&payload.StringAttributes,
			&payload.NumericAttributes,
		)
		if err != nil {
			return nil, fmt.Errorf("failed to scan payload: %w", err)
		}
		blockData.Payloads = append(blockData.Payloads, payload)
	}

	return blockData, nil
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
func loadBlockPool(sourceDb *sql.DB) error {
	fmt.Println("Loading entity pool into memory...")
	availableEntityKeys, err := getAvailableEntityKeys(sourceDb)
	if err != nil {
		return fmt.Errorf("failed to get available entity keys: %w", err)
	}

	fmt.Printf("Found %d entity keys in source database\n", len(availableEntityKeys))

	if len(availableEntityKeys) == 0 {
		return fmt.Errorf("no entities found in source database")
	}

	// Randomly select entity keys to form blocks
	// Each "block" will contain a random group of entities
	entitiesPerBlock := 100 // Approximate entities per block
	totalEntitiesToLoad := blockPoolSize * entitiesPerBlock
	if len(availableEntityKeys) < totalEntitiesToLoad {
		totalEntitiesToLoad = len(availableEntityKeys)
	}

	// Shuffle entity keys
	rand.Shuffle(len(availableEntityKeys), func(i, j int) {
		availableEntityKeys[i], availableEntityKeys[j] = availableEntityKeys[j], availableEntityKeys[i]
	})
	selectedEntityKeys := availableEntityKeys[:totalEntitiesToLoad]

	fmt.Printf("Loading %d entities into memory (forming ~%d blocks)...\n", totalEntitiesToLoad, blockPoolSize)
	loadStartTime := time.Now()

	// Group entities into blocks
	blockPool = make([]BlockData, 0, blockPoolSize)
	for i := 0; i < len(selectedEntityKeys); i += entitiesPerBlock {
		end := i + entitiesPerBlock
		if end > len(selectedEntityKeys) {
			end = len(selectedEntityKeys)
		}

		entityKeysForBlock := selectedEntityKeys[i:end]
		blockData, err := readEntityData(sourceDb, entityKeysForBlock)
		if err != nil {
			return fmt.Errorf("failed to read entities: %w", err)
		}

		if len(blockData.Payloads) > 0 {
			blockPool = append(blockPool, *blockData)
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
	store, err := sqlitestore.NewSQLiteStore(logger, targetDbPath, 7)
	if err != nil {
		return fmt.Errorf("failed to initialize target store: %w", err)
	}
	targetStore = store

	// Create shared PushIterator for all blocks
	targetPushIterator = pusher.NewPushIterator()

	// Create context for FollowEvents
	targetFollowEventsCtx, targetFollowEventsCancel = context.WithCancel(context.Background())

	// Start FollowEvents in a separate goroutine - it will run continuously
	go func() {
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

			// Parse string and numeric attributes from JSON
			// The structure is: {"Values": {"key1": "value1", "key2": "value2"}}
			type AttributesWrapper struct {
				Values map[string]interface{} `json:"Values"`
			}

			var stringAttrs map[string]string = make(map[string]string)
			var numericAttrs map[string]float64 = make(map[string]float64)

			if payload.StringAttributes != "" {
				var wrapper AttributesWrapper
				if err := json.Unmarshal([]byte(payload.StringAttributes), &wrapper); err == nil {
					if wrapper.Values != nil {
						for k, v := range wrapper.Values {
							if strVal, ok := v.(string); ok {
								stringAttrs[k] = strVal
							}
						}
					}
				}
			}

			if payload.NumericAttributes != "" {
				var wrapper AttributesWrapper
				if err := json.Unmarshal([]byte(payload.NumericAttributes), &wrapper); err == nil {
					if wrapper.Values != nil {
						for k, v := range wrapper.Values {
							// Try to convert to float64
							switch val := v.(type) {
							case float64:
								numericAttrs[k] = val
							case int:
								numericAttrs[k] = float64(val)
							case int64:
								numericAttrs[k] = float64(val)
							case string:
								// Try to parse as number
								if numVal, err := strconv.ParseFloat(val, 64); err == nil {
									numericAttrs[k] = numVal
								}
							}
						}
					}
				}
			}

			// Convert numeric attributes to uint64
			numericAttrsUint64 := make(map[string]uint64)
			for k, v := range numericAttrs {
				numericAttrsUint64[k] = uint64(v)
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

			// Extract owner from string attributes if present
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
	info, err := os.Stat(targetDbPath)
	if err != nil {
		return 0
	}
	return info.Size()
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

	// Calculate totals for logging
	batchPayloads := 0
	batchStringAttrs := 0
	batchNumericAttrs := 0

	for _, blockData := range blocksToReplicate {
		batchPayloads += len(blockData.Payloads)

		// Count attributes from JSON in payloads
		// The structure is: {"Values": {"key1": "value1", "key2": "value2"}}
		type AttributesWrapper struct {
			Values map[string]interface{} `json:"Values"`
		}

		for _, payload := range blockData.Payloads {
			if payload.StringAttributes != "" {
				var wrapper AttributesWrapper
				if err := json.Unmarshal([]byte(payload.StringAttributes), &wrapper); err == nil {
					if wrapper.Values != nil {
						batchStringAttrs += len(wrapper.Values)
					}
				}
			}
			if payload.NumericAttributes != "" {
				var wrapper AttributesWrapper
				if err := json.Unmarshal([]byte(payload.NumericAttributes), &wrapper); err == nil {
					if wrapper.Values != nil {
						batchNumericAttrs += len(wrapper.Values)
					}
				}
			}
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

	fmt.Println("Opening source database (read-only)...")
	sourceDb, err := sql.Open("sqlite3", sourceDbPath+"?mode=ro")
	if err != nil {
		return fmt.Errorf("failed to open source database: %w", err)
	}
	defer sourceDb.Close()

	// Load block pool into memory
	if err := loadBlockPool(sourceDb); err != nil {
		return err
	}

	// Initialize target database
	if err := initializeTargetDatabase(targetDbPath); err != nil {
		return fmt.Errorf("failed to initialize target database: %w", err)
	}
	defer func() {
		if targetFollowEventsCancel != nil {
			targetFollowEventsCancel()
		}
		if targetPushIterator != nil {
			targetPushIterator.Close()
		}
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
