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

// getAvailableBlocks gets all available block numbers from source database
func getAvailableBlocks(sourceDb *sql.DB) ([]int64, error) {
	query := `
		SELECT from_block 
		FROM payloads 
		GROUP BY from_block
		HAVING COUNT(*) < 1500
		ORDER BY from_block
	`
	rows, err := sourceDb.Query(query)
	if err != nil {
		return nil, fmt.Errorf("failed to query available blocks: %w", err)
	}
	defer rows.Close()

	var blocks []int64
	for rows.Next() {
		var block int64
		if err := rows.Scan(&block); err != nil {
			return nil, fmt.Errorf("failed to scan block: %w", err)
		}
		blocks = append(blocks, block)
	}
	return blocks, rows.Err()
}

// readBlockData reads all payloads for a specific block from source database
func readBlockData(sourceDb *sql.DB, fromBlock int64) (*BlockData, error) {
	blockData := &BlockData{}

	// Read payloads for the given block
	payloadsQuery := `
		SELECT entity_key, payload, content_type, string_attributes, numeric_attributes
		FROM payloads
		WHERE from_block = ?
	`

	rows, err := sourceDb.Query(payloadsQuery, fromBlock)
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

// loadBlockPool loads a pool of random blocks into memory
func loadBlockPool(sourceDb *sql.DB) error {
	fmt.Println("Loading block pool into memory...")
	availableBlocks, err := getAvailableBlocks(sourceDb)
	if err != nil {
		return fmt.Errorf("failed to get available blocks: %w", err)
	}

	fmt.Printf("Found %d blocks in source database\n", len(availableBlocks))

	if len(availableBlocks) == 0 {
		return fmt.Errorf("no blocks found in source database")
	}

	// Randomly select blockPoolSize blocks (or all if less available)
	blocksToLoad := blockPoolSize
	if len(availableBlocks) < blockPoolSize {
		blocksToLoad = len(availableBlocks)
	}

	// Shuffle and take first blocksToLoad
	rand.Shuffle(len(availableBlocks), func(i, j int) {
		availableBlocks[i], availableBlocks[j] = availableBlocks[j], availableBlocks[i]
	})
	selectedBlocks := availableBlocks[:blocksToLoad]

	fmt.Printf("Loading %d blocks into memory...\n", blocksToLoad)
	loadStartTime := time.Now()

	blockPool = make([]BlockData, 0, blocksToLoad)
	for _, blockNumber := range selectedBlocks {
		blockData, err := readBlockData(sourceDb, blockNumber)
		if err != nil {
			return fmt.Errorf("failed to read block %d: %w", blockNumber, err)
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
			var stringAttrs map[string]string
			var numericAttrs map[string]float64

			if payload.StringAttributes != "" {
				if err := json.Unmarshal([]byte(payload.StringAttributes), &stringAttrs); err != nil {
					stringAttrs = make(map[string]string)
				}
			} else {
				stringAttrs = make(map[string]string)
			}

			if payload.NumericAttributes != "" {
				if err := json.Unmarshal([]byte(payload.NumericAttributes), &numericAttrs); err != nil {
					numericAttrs = make(map[string]float64)
				}
			} else {
				numericAttrs = make(map[string]float64)
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

// replicateRandomBlock processes a single random block from the pool
func replicateRandomBlock(targetBlockNumber int64) (int, int, int, float64, error) {
	if len(blockPool) == 0 {
		return 0, 0, 0, 0, fmt.Errorf("block pool is empty")
	}

	blockStartTime := time.Now()

	// Select a random block from the pool
	randomIndex := rand.Intn(len(blockPool))
	blockData := blockPool[randomIndex]

	// Calculate totals for logging
	blockPayloads := len(blockData.Payloads)
	blockStringAttrs := 0
	blockNumericAttrs := 0

	// Count attributes from JSON in payloads
	for _, payload := range blockData.Payloads {
		if payload.StringAttributes != "" {
			var strAttrs map[string]interface{}
			if err := json.Unmarshal([]byte(payload.StringAttributes), &strAttrs); err == nil {
				blockStringAttrs += len(strAttrs)
			}
		}
		if payload.NumericAttributes != "" {
			var numAttrs map[string]interface{}
			if err := json.Unmarshal([]byte(payload.NumericAttributes), &numAttrs); err == nil {
				blockNumericAttrs += len(numAttrs)
			}
		}
	}

	// Write the block (single block in a batch)
	blocksToReplicate := []BlockData{blockData}
	if err := writeReplicatedBlockBatch(blocksToReplicate, targetBlockNumber); err != nil {
		return 0, 0, 0, 0, fmt.Errorf("failed to write block: %w", err)
	}

	blockDuration := time.Since(blockStartTime)

	return blockPayloads, blockStringAttrs, blockNumericAttrs, float64(blockDuration.Nanoseconds()) / 1e6, nil
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

	fmt.Printf("Starting block replicator (target: %d blocks)...\n", numBlocks)

	startTime := time.Now()
	targetBlockNumber := int64(1)

	// Continuously process random blocks until we reach the target number of blocks
	for totalBlocksReplicated < numBlocks {
		// Process a single random block
		blockPayloads, blockStringAttrs, blockNumericAttrs, blockDuration, err := replicateRandomBlock(targetBlockNumber)
		if err != nil {
			return fmt.Errorf("failed to process block: %w", err)
		}

		totalBlocksReplicated++
		totalPayloads += blockPayloads
		totalStringAttrs += blockStringAttrs
		totalNumericAttrs += blockNumericAttrs
		targetBlockNumber++

		// Write CSV log entry
		outputDbSize := getOutputDbSize(targetDbPath)
		if err := writeCsvRow(blockPayloads, blockStringAttrs, blockNumericAttrs, 0, blockDuration, outputDbSize); err != nil {
			fmt.Printf("Warning: Failed to write CSV row: %v\n", err)
		}

		message := fmt.Sprintf("[BLOCK] Processed block %d: %d payloads, %d str attrs, %d num attrs - %.2fms",
			targetBlockNumber-1, blockPayloads, blockStringAttrs, blockNumericAttrs, blockDuration)
		fmt.Println(message)

		// Warn if block processing takes more than 1000ms
		if blockDuration > 1000 {
			fmt.Printf("⚠️  WARNING: Block processing took %.2fms\n", blockDuration)
		}
	}

	totalTime := time.Since(startTime).Seconds()
	fmt.Printf("\nTotal time: %.2fs\n", totalTime)
	fmt.Printf("Blocks per second: %.2f\n", float64(totalBlocksReplicated)/totalTime)

	printFinalStatistics()

	return nil
}
