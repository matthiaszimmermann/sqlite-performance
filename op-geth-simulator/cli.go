package main

import (
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	mathrand "math/rand"
	"net/http"
	"os"
	"strconv"
	"time"
)

// predefinedWords is a list of 20 words for string attribute values
var predefinedWords = []string{
	"alpha", "beta", "gamma", "delta", "epsilon",
	"zeta", "eta", "theta", "iota", "kappa",
	"lambda", "mu", "nu", "xi", "omicron",
	"pi", "rho", "sigma", "tau", "upsilon",
}

// randomInt generates a random integer between min and max (inclusive)
func randomInt(min, max int) int {
	return min + mathrand.Intn(max-min+1)
}

// randomAddress generates a random Ethereum-like address
func randomAddress() string {
	bytes := make([]byte, 20)
	rand.Read(bytes)
	return "0x" + hex.EncodeToString(bytes)
}

// randomWord returns a random word from predefinedWords
func randomWord() string {
	return predefinedWords[mathrand.Intn(len(predefinedWords))]
}

// generateAnnotations generates string and numeric annotations
func generateAnnotations(numAttributes int) (map[string]string, map[string]float64) {
	numStringAttrs := numAttributes / 2
	numNumericAttrs := numAttributes - numStringAttrs

	stringAnnotations := make(map[string]string)
	numericAnnotations := make(map[string]float64)

	// Generate string annotations with constant keys
	for i := 0; i < numStringAttrs; i++ {
		key := fmt.Sprintf("attr_str_%d", i)
		stringAnnotations[key] = randomWord()
	}

	// Generate numeric annotations with constant keys
	for i := 0; i < numNumericAttrs; i++ {
		key := fmt.Sprintf("attr_num_%d", i)
		numericAnnotations[key] = float64(randomInt(0, 10))
	}

	return stringAnnotations, numericAnnotations
}

// generateRandomBytes generates random bytes of the specified size in KB
func generateRandomBytes(sizeInKB float64) []byte {
	sizeInBytes := int(sizeInKB * 1024)
	bytes := make([]byte, sizeInBytes)
	rand.Read(bytes)
	return bytes
}

// getServerURL returns the server URL from environment or default
func getServerURL() string {
	url := os.Getenv("SERVER_URL")
	if url == "" {
		url = "http://localhost:3000"
	}
	return url
}

// addEntities adds entities via HTTP requests to the server
func addEntities(count, numAttributes int, maxSizeKB float64) error {
	numStringAttrs := numAttributes / 2
	numNumericAttrs := numAttributes - numStringAttrs

	fmt.Printf("Adding %d entities with random payload sizes (0.5KB - %.1fKB) and %d attributes (%d string, %d numeric)...\n\n",
		count, maxSizeKB, numAttributes, numStringAttrs, numNumericAttrs)

	serverURL := getServerURL()
	fmt.Printf("Connecting to server: %s\n", serverURL)

	// Check if server is running
	if err := checkServerHealth(serverURL); err != nil {
		return fmt.Errorf("server is not available at %s: %w\nPlease make sure the server is running (go run . or ./op-geth-simulator)", serverURL, err)
	}

	startTime := time.Now()
	successCount := 0
	errorCount := 0

	for i := 0; i < count; i++ {
		// Generate random payload size between 0.5KB and maxSizeKB
		payloadSizeKB := 0.5 + mathrand.Float64()*(maxSizeKB-0.5)
		payload := generateRandomBytes(payloadSizeKB)

		// Generate annotations
		stringAnnotations, numericAnnotations := generateAnnotations(numAttributes)

		// Convert numeric annotations to interface{} for JSON
		numericAnnotationsInterface := make(map[string]interface{})
		for k, v := range numericAnnotations {
			numericAnnotationsInterface[k] = v
		}

		// Create write request
		request := EntityWriteRequest{
			Key:                fmt.Sprintf("cli-entity-%d-%d-%s", time.Now().UnixNano(), i, randomString(7)),
			ExpiresIn:          int64(randomInt(3600, 86400*7)), // 1 hour to 7 days in blocks
			Payload:            base64.StdEncoding.EncodeToString(payload),
			ContentType:        "application/octet-stream",
			Deleted:            false,
			OwnerAddress:       randomAddress(),
			StringAnnotations:  stringAnnotations,
			NumericAnnotations: numericAnnotationsInterface,
		}

		// Send HTTP request
		if err := sendAddEntityRequest(serverURL, request); err != nil {
			errorCount++
			if errorCount <= 5 { // Only show first 5 errors
				fmt.Printf("\n✗ Error adding entity %d: %v\n", i+1, err)
			}
			continue
		}

		successCount++
		if (i+1)%100 == 0 || i == count-1 {
			progress := float64(i+1) / float64(count) * 100
			elapsed := time.Since(startTime).Seconds()
			fmt.Printf("\rProgress: %d/%d (%.1f%%) - Success: %d, Errors: %d - Elapsed: %.1fs",
				i+1, count, progress, successCount, errorCount, elapsed)
		}
	}

	fmt.Println()
	fmt.Printf("✓ Completed: %d entities queued via HTTP (Success: %d, Errors: %d)\n", count, successCount, errorCount)
	totalTime := time.Since(startTime).Seconds()
	if successCount > 0 {
		rate := float64(successCount) / totalTime
		fmt.Printf("  Total time: %.2fs\n", totalTime)
		fmt.Printf("  Queue rate: ~%.0f entities/second\n", rate)
	}

	return nil
}

// checkServerHealth checks if the server is running
func checkServerHealth(serverURL string) error {
	resp, err := http.Get(serverURL + "/health")
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("server returned status %d", resp.StatusCode)
	}
	return nil
}

// sendAddEntityRequest sends an HTTP POST request to add an entity
func sendAddEntityRequest(serverURL string, request EntityWriteRequest) error {
	jsonData, err := json.Marshal(request)
	if err != nil {
		return fmt.Errorf("failed to marshal request: %w", err)
	}

	resp, err := http.Post(serverURL+"/entities", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		return fmt.Errorf("HTTP request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusAccepted {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("server returned status %d: %s", resp.StatusCode, string(body))
	}

	return nil
}

// cleanDatabase cleans all data via HTTP request to the server
func cleanDatabase() error {
	fmt.Println("Cleaning all data from the database...")

	serverURL := getServerURL()
	fmt.Printf("Connecting to server: %s\n", serverURL)

	// Check if server is running
	if err := checkServerHealth(serverURL); err != nil {
		return fmt.Errorf("server is not available at %s: %w\nPlease make sure the server is running (go run . or ./op-geth-simulator)", serverURL, err)
	}

	// Send DELETE request
	req, err := http.NewRequest("DELETE", serverURL+"/entities/clean", nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("HTTP request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("server returned status %d: %s", resp.StatusCode, string(body))
	}

	fmt.Println("✓ All data has been cleaned from the database.")
	return nil
}

// queryEntities queries entities via HTTP request to the server
func queryEntities(ownerAddress string, stringAnnotations map[string]string, numericAnnotations map[string]interface{}, limit, offset int) error {
	serverURL := getServerURL()
	fmt.Printf("Querying entities from server: %s\n", serverURL)

	// Check if server is running
	if err := checkServerHealth(serverURL); err != nil {
		return fmt.Errorf("server is not available at %s: %w\nPlease make sure the server is running (go run . or ./op-geth-simulator)", serverURL, err)
	}

	// Create query request
	request := EntityQueryRequest{
		OwnerAddress:       ownerAddress,
		StringAnnotations:  stringAnnotations,
		NumericAnnotations: numericAnnotations,
		Limit:              limit,
		Offset:             offset,
	}

	jsonData, err := json.Marshal(request)
	if err != nil {
		return fmt.Errorf("failed to marshal request: %w", err)
	}

	// Send POST request
	resp, err := http.Post(serverURL+"/entities/query", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		return fmt.Errorf("HTTP request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("server returned status %d: %s", resp.StatusCode, string(body))
	}

	// Parse response
	var response struct {
		Entities []map[string]interface{} `json:"entities"`
		Count    int                      `json:"count"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&response); err != nil {
		return fmt.Errorf("failed to decode response: %w", err)
	}

	// Display results
	fmt.Printf("\n✓ Found %d entities (showing %d):\n\n", response.Count, len(response.Entities))
	for i, entity := range response.Entities {
		fmt.Printf("Entity %d:\n", i+1)
		if key, ok := entity["key"].(string); ok {
			fmt.Printf("  Key: %s\n", key)
		}
		if owner, ok := entity["ownerAddress"].(string); ok {
			fmt.Printf("  Owner: %s\n", owner)
		}
		if contentType, ok := entity["contentType"].(string); ok {
			fmt.Printf("  ContentType: %s\n", contentType)
		}
		if strAttrs, ok := entity["stringAnnotations"].(map[string]interface{}); ok && len(strAttrs) > 0 {
			fmt.Printf("  String Attributes: %v\n", strAttrs)
		}
		if numAttrs, ok := entity["numericAnnotations"].(map[string]interface{}); ok && len(numAttrs) > 0 {
			fmt.Printf("  Numeric Attributes: %v\n", numAttrs)
		}
		fmt.Println()
	}

	return nil
}

// getEntity retrieves an entity by key via HTTP request
func getEntity(key string) error {
	serverURL := getServerURL()
	fmt.Printf("Getting entity from server: %s\n", serverURL)

	// Check if server is running
	if err := checkServerHealth(serverURL); err != nil {
		return fmt.Errorf("server is not available at %s: %w\nPlease make sure the server is running (go run . or ./op-geth-simulator)", serverURL, err)
	}

	// Send GET request
	resp, err := http.Get(serverURL + "/entities/" + key)
	if err != nil {
		return fmt.Errorf("HTTP request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		fmt.Printf("✗ Entity not found: %s\n", key)
		return nil
	}

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("server returned status %d: %s", resp.StatusCode, string(body))
	}

	// Parse response
	var entity map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&entity); err != nil {
		return fmt.Errorf("failed to decode response: %w", err)
	}

	// Display entity
	fmt.Printf("\n✓ Entity found:\n\n")
	if key, ok := entity["key"].(string); ok {
		fmt.Printf("  Key: %s\n", key)
	}
	if owner, ok := entity["ownerAddress"].(string); ok {
		fmt.Printf("  Owner: %s\n", owner)
	}
	if contentType, ok := entity["contentType"].(string); ok {
		fmt.Printf("  ContentType: %s\n", contentType)
	}
	if strAttrs, ok := entity["stringAnnotations"].(map[string]interface{}); ok && len(strAttrs) > 0 {
		fmt.Printf("  String Attributes: %v\n", strAttrs)
	}
	if numAttrs, ok := entity["numericAnnotations"].(map[string]interface{}); ok && len(numAttrs) > 0 {
		fmt.Printf("  Numeric Attributes: %v\n", numAttrs)
	}
	fmt.Println()

	return nil
}

// countEntities gets the entity count via HTTP request
func countEntities() error {
	serverURL := getServerURL()
	fmt.Printf("Getting entity count from server: %s\n", serverURL)

	// Check if server is running
	if err := checkServerHealth(serverURL); err != nil {
		return fmt.Errorf("server is not available at %s: %w\nPlease make sure the server is running (go run . or ./op-geth-simulator)", serverURL, err)
	}

	// Send GET request
	resp, err := http.Get(serverURL + "/entities/count")
	if err != nil {
		return fmt.Errorf("HTTP request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("server returned status %d: %s", resp.StatusCode, string(body))
	}

	// Parse response
	var response struct {
		Count int `json:"count"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&response); err != nil {
		return fmt.Errorf("failed to decode response: %w", err)
	}

	fmt.Printf("\n✓ Total entities in database: %d\n\n", response.Count)
	return nil
}

// printUsage prints CLI usage information
func printUsage() {
	fmt.Println(`
Usage: go run . cli <command> [options]

Commands:
  add <count> [attributes] [max-size]   Add N entities via HTTP to the server
                                        Entities are queued and processed by block processor
  query [options]                        Query entities via HTTP
  get <key>                             Get a single entity by key via HTTP
  count                                 Get total entity count via HTTP
  clean                                 Clean all data via HTTP

Arguments for add:
  count                                 Number of entities to add
  attributes                            Number of attributes per entity (default: 10)
                                        Half will be string attributes, half numeric
  max-size                              Maximum payload size in KB (default: 120)
                                        Payload sizes will be random between 0.5KB and max-size

Arguments for query:
  --owner <address>                    Filter by owner address
  --string-attr <key>=<value>          Filter by string attribute (can be used multiple times)
  --numeric-attr <key>=<value>          Filter by numeric attribute (can be used multiple times)
                                        For range queries, use operators: >=, <=, >, <, !=
  --limit <n>                           Maximum number of results (default: 100)
  --offset <n>                          Offset for pagination (default: 0)

Examples:
  go run . cli add 100                  Add 100 entities with 10 attributes, max 120KB payload
  go run . cli add 100 20                Add 100 entities with 20 attributes, max 120KB payload
  go run . cli add 1000 50 50            Add 1000 entities with 50 attributes, max 50KB payload
  go run . cli query                     Query all entities (first 100)
  go run . cli query --owner 0x123...    Query entities by owner
  go run . cli query --string-attr attr_str_0=alpha --limit 10
  go run . cli query --numeric-attr attr_num_0=5 --limit 20
  go run . cli query --numeric-attr attr_num_0=">=5" --limit 20
  go run . cli get cli-entity-123        Get entity by key
  go run . cli count                     Get total entity count
  go run . cli clean                     Clean all data

Environment variables:
  SERVER_URL                            Server URL (default: http://localhost:3000)
                                        Make sure the server is running first!

Note: The server must be running before using CLI commands.
      Start it with: go run . or ./op-geth-simulator`)
}

// parseQueryArgs parses query command arguments
func parseQueryArgs(args []string) (string, map[string]string, map[string]interface{}, int, int, error) {
	var ownerAddress string
	stringAnnotations := make(map[string]string)
	numericAnnotations := make(map[string]interface{})
	limit := 100
	offset := 0

	for i := 0; i < len(args); i++ {
		arg := args[i]
		switch arg {
		case "--owner":
			if i+1 >= len(args) {
				return "", nil, nil, 0, 0, fmt.Errorf("--owner requires a value")
			}
			ownerAddress = args[i+1]
			i++
		case "--string-attr":
			if i+1 >= len(args) {
				return "", nil, nil, 0, 0, fmt.Errorf("--string-attr requires key=value")
			}
			kv := args[i+1]
			parts := splitKeyValue(kv)
			if len(parts) != 2 {
				return "", nil, nil, 0, 0, fmt.Errorf("--string-attr format should be key=value")
			}
			stringAnnotations[parts[0]] = parts[1]
			i++
		case "--numeric-attr":
			if i+1 >= len(args) {
				return "", nil, nil, 0, 0, fmt.Errorf("--numeric-attr requires key=value")
			}
			kv := args[i+1]
			parts := splitKeyValue(kv)
			if len(parts) != 2 {
				return "", nil, nil, 0, 0, fmt.Errorf("--numeric-attr format should be key=value or key=operator")
			}
			// Try to parse as number first, otherwise treat as string (for operators like >=5)
			if numVal, err := strconv.ParseFloat(parts[1], 64); err == nil {
				numericAnnotations[parts[0]] = numVal
			} else {
				numericAnnotations[parts[0]] = parts[1]
			}
			i++
		case "--limit":
			if i+1 >= len(args) {
				return "", nil, nil, 0, 0, fmt.Errorf("--limit requires a number")
			}
			var err error
			limit, err = strconv.Atoi(args[i+1])
			if err != nil || limit <= 0 {
				return "", nil, nil, 0, 0, fmt.Errorf("--limit must be a positive number")
			}
			i++
		case "--offset":
			if i+1 >= len(args) {
				return "", nil, nil, 0, 0, fmt.Errorf("--offset requires a number")
			}
			var err error
			offset, err = strconv.Atoi(args[i+1])
			if err != nil || offset < 0 {
				return "", nil, nil, 0, 0, fmt.Errorf("--offset must be a non-negative number")
			}
			i++
		default:
			return "", nil, nil, 0, 0, fmt.Errorf("unknown argument: %s", arg)
		}
	}

	return ownerAddress, stringAnnotations, numericAnnotations, limit, offset, nil
}

// splitKeyValue splits a key=value string
func splitKeyValue(kv string) []string {
	for i := 0; i < len(kv); i++ {
		if kv[i] == '=' {
			return []string{kv[:i], kv[i+1:]}
		}
	}
	return []string{kv}
}

// RunCLI runs the CLI command
func RunCLI() {
	args := os.Args[2:] // Skip "cli" command

	if len(args) == 0 {
		printUsage()
		os.Exit(0)
	}

	command := args[0]
	commandArgs := args[1:]

	switch command {
	case "add":
		if len(commandArgs) < 1 {
			fmt.Println("Error: Please provide a valid positive number for entity count")
			fmt.Println("Example: go run . cli add 100")
			os.Exit(1)
		}

		count, err := strconv.Atoi(commandArgs[0])
		if err != nil || count <= 0 {
			fmt.Println("Error: Please provide a valid positive number for entity count")
			fmt.Println("Example: go run . cli add 100")
			os.Exit(1)
		}

		numAttributes := 10
		if len(commandArgs) >= 2 {
			numAttributes, err = strconv.Atoi(commandArgs[1])
			if err != nil || numAttributes <= 0 {
				fmt.Println("Error: Number of attributes must be a positive number")
				fmt.Println("Example: go run . cli add 100 20")
				os.Exit(1)
			}
		}

		maxSizeKB := 120.0
		if len(commandArgs) >= 3 {
			maxSizeKB, err = strconv.ParseFloat(commandArgs[2], 64)
			if err != nil || maxSizeKB < 0.5 {
				fmt.Println("Error: Max size must be a positive number >= 0.5")
				fmt.Println("Example: go run . cli add 100 20 50")
				os.Exit(1)
			}
		}

		if err := addEntities(count, numAttributes, maxSizeKB); err != nil {
			log.Fatalf("Error: %v", err)
		}

	case "query":
		ownerAddress, stringAnnotations, numericAnnotations, limit, offset, err := parseQueryArgs(commandArgs)
		if err != nil {
			fmt.Printf("Error parsing query arguments: %v\n", err)
			fmt.Println("Example: go run . cli query --owner 0x123... --limit 10")
			os.Exit(1)
		}

		if err := queryEntities(ownerAddress, stringAnnotations, numericAnnotations, limit, offset); err != nil {
			log.Fatalf("Error: %v", err)
		}

	case "get":
		if len(commandArgs) < 1 {
			fmt.Println("Error: Please provide an entity key")
			fmt.Println("Example: go run . cli get cli-entity-123")
			os.Exit(1)
		}

		if err := getEntity(commandArgs[0]); err != nil {
			log.Fatalf("Error: %v", err)
		}

	case "count":
		if err := countEntities(); err != nil {
			log.Fatalf("Error: %v", err)
		}

	case "clean":
		if err := cleanDatabase(); err != nil {
			log.Fatalf("Error: %v", err)
		}

	case "help", "--help", "-h":
		printUsage()

	default:
		fmt.Printf("Unknown command: %s\n", command)
		printUsage()
		os.Exit(1)
	}
}
