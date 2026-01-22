package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	arkivevents "github.com/Arkiv-Network/arkiv-events"
	sqlitestore "github.com/Arkiv-Network/sqlite-bitmap-store"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
)

var (
	storeInstance *sqlitestore.SQLiteStore
	storeOnce     sync.Once
	storeMutex    sync.RWMutex
)

// InitStore initializes the sqlite-bitmap-store
func InitStore(dbPath string) error {
	var err error
	storeOnce.Do(func() {
		// Use the custom logger that routes logs to appropriate files
		logger := GetStoreLogger()

		storeInstance, err = sqlitestore.NewSQLiteStore(logger, dbPath, 7)
		if err != nil {
			log.Printf("Failed to initialize store: %v", err)
		}
	})
	return err
}

// CloseStore closes the store
func CloseStore() error {
	storeMutex.Lock()
	defer storeMutex.Unlock()
	if storeInstance != nil {
		return storeInstance.Close()
	}
	return nil
}

// GetEntityByKey retrieves an entity by its key using QueryEntities
func GetEntityByKey(key string) (*Entity, error) {
	startTime := time.Now()
	defer func() {
		duration := time.Since(startTime)
		logDbOperation(fmt.Sprintf("getEntityByKey(key=%s)", key), duration)
		logQuery("getEntityByKey", duration, map[string]interface{}{
			"$key": key,
		})
	}()

	storeMutex.RLock()
	s := storeInstance
	storeMutex.RUnlock()

	if s == nil {
		return nil, fmt.Errorf("store not initialized")
	}

	ctx := context.Background()
	currentBlock := GetCurrentBlockNumber()

	arkivQuery := fmt.Sprintf(`$key = "%s"`, key)

	atBlock := uint64(currentBlock)
	resultsPerPage := uint64(1)
	options := &sqlitestore.Options{
		AtBlock:        &atBlock,
		ResultsPerPage: &resultsPerPage,
	}

	response, err := s.QueryEntities(ctx, arkivQuery, options)
	if err != nil {
		return nil, fmt.Errorf("failed to query entity: %w", err)
	}

	if len(response.Data) == 0 {
		// Entity not found
		return nil, nil
	}

	// Parse the first result
	return parseEntityData(response.Data[0], key)
}

// QueryEntities queries entities using NewQueries
func QueryEntities(ownerAddress string, stringAnnotations map[string]string, numericAnnotations map[string]interface{}, limit, offset int) ([]*Entity, error) {
	startTime := time.Now()
	defer func() {
		duration := time.Since(startTime)
		logDbOperation(fmt.Sprintf("queryEntities(limit=%d, offset=%d)", limit, offset), duration)
		logQuery("queryEntities", duration, map[string]interface{}{
			"ownerAddress":       ownerAddress,
			"stringAnnotations":  stringAnnotations,
			"numericAnnotations": numericAnnotations,
			"limit":              limit,
			"offset":             offset,
		})
	}()

	storeMutex.RLock()
	s := storeInstance
	storeMutex.RUnlock()

	if s == nil {
		return nil, fmt.Errorf("store not initialized")
	}

	ctx := context.Background()
	currentBlock := GetCurrentBlockNumber()

	// Build Arkiv query string from filter parameters
	arkivQuery := buildArkivQuery(ownerAddress, stringAnnotations, numericAnnotations)

	// Use SQLiteStore.QueryEntities with proper Options
	atBlock := uint64(currentBlock)
	resultsPerPage := uint64(limit)
	options := &sqlitestore.Options{
		AtBlock:        &atBlock,
		ResultsPerPage: &resultsPerPage,
	}

	response, err := s.QueryEntities(ctx, arkivQuery, options)
	if err != nil {
		return nil, fmt.Errorf("failed to query entities: %w", err)
	}

	// Convert QueryResponse.Data ([]json.RawMessage) to entities
	entities := make([]*Entity, 0, len(response.Data))
	for _, dataItem := range response.Data {
		entity, err := parseEntityData(dataItem, "")
		if err != nil {
			continue // Skip invalid entries
		}
		entities = append(entities, entity)
	}

	return entities, nil
}

// buildArkivQuery builds an Arkiv query string from filter parameters
// Based on the EntityData structure, owner is stored as $owner in string attributes
func buildArkivQuery(
	ownerAddress string,
	stringAnnotations map[string]string,
	numericAnnotations map[string]interface{},
) string {
	conditions := []string{}

	// Filter by owner if provided (stored as $owner in string attributes)
	if ownerAddress != "" {
		// Try both ownerAddress and $owner for compatibility
		conditions = append(conditions, fmt.Sprintf(`$owner = "%s"`, ownerAddress))
	}

	// Filter by string annotations (equality)
	if stringAnnotations != nil {
		for k, v := range stringAnnotations {
			// Escape double quotes in string values
			escapedValue := fmt.Sprintf("%q", v)
			conditions = append(conditions, fmt.Sprintf(`%s = %s`, k, escapedValue))
		}
	}

	// Filter by numeric annotations (equality or range)
	if numericAnnotations != nil {
		for k, v := range numericAnnotations {
			if numVal, ok := v.(float64); ok {
				// Exact match
				conditions = append(conditions, fmt.Sprintf("%s = %g", k, numVal))
			} else if strVal, ok := v.(string); ok {
				// Range query with operator (e.g., ">=8", "<=32", ">16", "<64", "!=0")
				conditions = append(conditions, fmt.Sprintf("%s %s", k, strVal))
			}
		}
	}

	// Join all conditions with AND
	if len(conditions) == 0 {
		return ""
	}
	result := conditions[0]
	for i := 1; i < len(conditions); i++ {
		result += " AND " + conditions[i]
	}
	log.Printf("Query has been built: %s", result)

	return result
}

// CountEntities counts the total number of entities using QueryEntities
func CountEntities() (int, error) {
	startTime := time.Now()
	defer func() {
		duration := time.Since(startTime)
		logDbOperation(fmt.Sprintf("countEntities"), duration)
	}()

	storeMutex.RLock()
	s := storeInstance
	storeMutex.RUnlock()

	if s == nil {
		return 0, fmt.Errorf("store not initialized")
	}

	ctx := context.Background()
	currentBlock := GetCurrentBlockNumber()

	// Query all entities with empty query to get total count
	atBlock := uint64(currentBlock)
	resultsPerPage := uint64(1) // We only need the count
	options := &sqlitestore.Options{
		AtBlock:        &atBlock,
		ResultsPerPage: &resultsPerPage,
	}

	response, err := s.QueryEntities(ctx, "", options)
	if err != nil {
		return 0, fmt.Errorf("failed to count entities: %w", err)
	}

	return int(len(response.Data)), nil
}

// GetExpiredEntities retrieves entity key hashes whose expiration is less than or equal to the given block number
// Only returns entity key hashes (not full entity data) for performance
func GetExpiredEntities(blockNumber int64) ([]common.Hash, error) {
	storeMutex.RLock()
	s := storeInstance
	storeMutex.RUnlock()

	if s == nil {
		return nil, fmt.Errorf("store not initialized")
	}

	ctx := context.Background()
	currentBlock := GetCurrentBlockNumber()

	// Query for entities that expire at or before this block number
	// Expiration is stored as $expiration in numeric attributes
	// Use <= operator to get all entities that have expired
	arkivQuery := fmt.Sprintf("$expiration = %d", blockNumber)
	atBlock := uint64(currentBlock)
	resultsPerPage := uint64(10000) // Large limit to get all expired entities

	// Use IncludeData to only fetch the key field for performance
	includeData := &sqlitestore.IncludeData{
		Key:                         true,
		Attributes:                  false,
		SyntheticAttributes:         false,
		Payload:                     false,
		ContentType:                 false,
		Expiration:                  false,
		Owner:                       false,
		CreatedAtBlock:              false,
		LastModifiedAtBlock:         false,
		TransactionIndexInBlock:     false,
		OperationIndexInTransaction: false,
	}

	options := &sqlitestore.Options{
		AtBlock:        &atBlock,
		ResultsPerPage: &resultsPerPage,
		IncludeData:    includeData,
	}

	response, err := s.QueryEntities(ctx, arkivQuery, options)
	if err != nil {
		return nil, fmt.Errorf("failed to query expired entities: %w", err)
	}

	// Extract only entity key hashes from the response
	entityKeyHashes := make([]common.Hash, 0, len(response.Data))
	for _, dataItem := range response.Data {
		var entityData struct {
			Key *common.Hash `json:"key,omitempty"`
		}

		if err := json.Unmarshal(dataItem, &entityData); err != nil {
			continue // Skip invalid entries
		}

		if entityData.Key != nil {
			// Store the hash directly (this is what we need for delete operations)
			entityKeyHashes = append(entityKeyHashes, *entityData.Key)
		}
	}

	return entityKeyHashes, nil
}

// GetCurrentBlockNumber gets the current block number
func GetCurrentBlockNumber() int64 {
	storeMutex.RLock()
	s := storeInstance
	storeMutex.RUnlock()

	if s == nil {
		return 1
	}

	ctx := context.Background()

	// Use NewQueries.GetLastBlock to get current block number
	block, err := s.GetLastBlock(ctx)
	if err != nil {
		return 1
	}
	return int64(block)
}

// FollowEvents passes a block batch to the store's followEvents method
func FollowEvents(ctx context.Context, batchIterator arkivevents.BatchIterator) error {
	storeMutex.RLock()
	s := storeInstance
	storeMutex.RUnlock()

	if s == nil {
		return fmt.Errorf("store not initialized")
	}

	// Pass the block batch to the store's followEvents method
	return s.FollowEvents(ctx, batchIterator)
}

// CleanAllData removes all data from the store
func CleanAllData() error {
	storeMutex.RLock()
	s := storeInstance
	storeMutex.RUnlock()

	if s == nil {
		return fmt.Errorf("store not initialized")
	}

	// CleanAllData - use SQLiteStore methods to clear data
	// Note: The sqlite-bitmap-store may not have a direct Clear method
	// This might need to be implemented differently or may not be available
	// For now, return an error indicating it's not implemented
	return fmt.Errorf("CleanAllData not implemented - sqlite-bitmap-store does not provide a Clear method")
}

// parseEntityData parses EntityData from json.RawMessage into Entity
func parseEntityData(data json.RawMessage, fallbackKey string) (*Entity, error) {
	var entityData struct {
		Key                         *common.Hash    `json:"key,omitempty"`
		Value                       hexutil.Bytes   `json:"value,omitempty"`
		ContentType                 *string         `json:"contentType,omitempty"`
		ExpiresAt                   *uint64         `json:"expiresAt,omitempty"`
		Owner                       *common.Address `json:"owner,omitempty"`
		CreatedAtBlock              *uint64         `json:"createdAtBlock,omitempty"`
		LastModifiedAtBlock         *uint64         `json:"lastModifiedAtBlock,omitempty"`
		TransactionIndexInBlock     *uint64         `json:"transactionIndexInBlock,omitempty"`
		OperationIndexInTransaction *uint64         `json:"operationIndexInTransaction,omitempty"`
		StringAttributes            []struct {
			Key   string `json:"key"`
			Value string `json:"value"`
		} `json:"stringAttributes,omitempty"`
		NumericAttributes []struct {
			Key   string `json:"key"`
			Value uint64 `json:"value"`
		} `json:"numericAttributes,omitempty"`
	}

	if err := json.Unmarshal(data, &entityData); err != nil {
		return nil, fmt.Errorf("failed to unmarshal entity data: %w", err)
	}

	entity := &Entity{
		StringAnnotations:  make(map[string]string),
		NumericAnnotations: make(map[string]float64),
	}

	// Set key - use hash if available, otherwise fallback
	if entityData.Key != nil {
		// Convert hash back to original key if possible, or use hash hex as key
		entity.Key = fallbackKey
		if entity.Key == "" {
			entity.Key = entityData.Key.Hex()
		}
	} else if fallbackKey != "" {
		entity.Key = fallbackKey
	}

	// Set payload
	if entityData.Value != nil {
		entity.Payload = []byte(entityData.Value)
	}

	// Set content type
	if entityData.ContentType != nil {
		entity.ContentType = *entityData.ContentType
	}

	// Set expiration
	if entityData.ExpiresAt != nil {
		entity.ExpiresAt = int64(*entityData.ExpiresAt)
	}

	// Set owner address
	if entityData.Owner != nil {
		entity.OwnerAddress = entityData.Owner.Hex()
	}

	// Set block numbers
	if entityData.CreatedAtBlock != nil {
		entity.CreatedAtBlock = int64(*entityData.CreatedAtBlock)
	}
	if entityData.LastModifiedAtBlock != nil {
		entity.LastModifiedAtBlock = int64(*entityData.LastModifiedAtBlock)
	}

	// Parse string attributes (excluding synthetic attributes starting with $)
	for _, attr := range entityData.StringAttributes {
		if !strings.HasPrefix(attr.Key, "$") {
			entity.StringAnnotations[attr.Key] = attr.Value
		}
	}

	// Parse numeric attributes (excluding synthetic attributes starting with $)
	for _, attr := range entityData.NumericAttributes {
		if !strings.HasPrefix(attr.Key, "$") {
			entity.NumericAnnotations[attr.Key] = float64(attr.Value)
		}
	}

	return entity, nil
}

// Helper function to encode payload to base64
func encodeBase64Payload(payload []byte) string {
	return base64.StdEncoding.EncodeToString(payload)
}
