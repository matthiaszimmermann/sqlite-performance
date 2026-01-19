package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/gorilla/mux"
)

// StartServer starts the HTTP server
func StartServer(port int, dbPath string, testname string) error {
	// Initialize store
	if err := InitStore(dbPath); err != nil {
		return fmt.Errorf("failed to initialize store: %w", err)
	}

	// Start block processor
	StartBlockProcessor(testname)

	// Setup graceful shutdown
	setupGracefulShutdown()

	// Create router
	r := mux.NewRouter()

	// Middleware to measure and log request time
	r.Use(requestLoggerMiddleware)

	// Health check
	r.HandleFunc("/health", healthHandler).Methods("GET")

	// Write entity endpoint
	r.HandleFunc("/entities", writeEntityHandler).Methods("POST")

	// Get entity by key endpoint
	r.HandleFunc("/entities/{key}", getEntityHandler).Methods("GET")

	// Update entity by key endpoint (partial update via merge + enqueue)
	r.HandleFunc("/entities/{key}", updateEntityHandler).Methods("PUT", "PATCH")

	// Query entities endpoint
	r.HandleFunc("/entities/query", queryEntitiesHandler).Methods("POST")

	// Count entities endpoint
	r.HandleFunc("/entities/count", countEntitiesHandler).Methods("GET")

	// Clean all data endpoint
	r.HandleFunc("/entities/clean", cleanAllDataHandler).Methods("DELETE")

	// Get receipt endpoint
	r.HandleFunc("/receipt/{id}", getReceiptHandler).Methods("GET")

	addr := fmt.Sprintf(":%d", port)
	fmt.Printf("Server starting on port %d...\n", port)
	fmt.Printf("Server running on http://localhost%s\n", addr)

	return http.ListenAndServe(addr, r)
}

// requestLoggerMiddleware logs request timing
func requestLoggerMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		startTime := time.Now()
		timestamp := time.Now().Format(time.RFC3339)

		// Log incoming request with debug level
		fmt.Printf("[%s] [DEBUG] [HTTP] Incoming request: %s %s\n", timestamp, r.Method, r.URL.Path)

		// Create a response writer wrapper to capture status code
		rw := &responseWriter{ResponseWriter: w, statusCode: http.StatusOK}

		next.ServeHTTP(rw, r)

		duration := time.Since(startTime)
		statusCode := rw.statusCode
		durationMs := duration.Milliseconds()

		// Determine log level based on status code
		level := "INFO"
		if statusCode >= 500 {
			level = "ERROR"
		} else if statusCode >= 400 {
			level = "WARN"
		}

		// Log request completion
		fmt.Printf("[%s] [%s] [HTTP] %s %s - %d - %dms\n",
			time.Now().Format(time.RFC3339),
			level,
			r.Method,
			r.URL.Path,
			statusCode,
			durationMs,
		)

		// Warn if request takes more than 500ms
		if duration > 500*time.Millisecond {
			logRequestWarning(r.Method, r.URL.Path, duration)
		}
	})
}

// responseWriter wraps http.ResponseWriter to capture status code
type responseWriter struct {
	http.ResponseWriter
	statusCode int
}

func (rw *responseWriter) WriteHeader(code int) {
	rw.statusCode = code
	rw.ResponseWriter.WriteHeader(code)
}

// healthHandler handles health check requests
func healthHandler(w http.ResponseWriter, r *http.Request) {
	timestamp := time.Now().Format(time.RFC3339)
	queueSize := writeQueue.GetQueueSize()

	fmt.Printf("[%s] [DEBUG] [HTTP] GET /health - Queue size: %d\n", timestamp, queueSize)

	response := map[string]interface{}{
		"status":    "ok",
		"queueSize": queueSize,
	}
	jsonResponse(w, http.StatusOK, response)
}

// writeEntityHandler handles entity write requests
func writeEntityHandler(w http.ResponseWriter, r *http.Request) {
	timestamp := time.Now().Format(time.RFC3339)
	queueSizeBefore := writeQueue.GetQueueSize()

	fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities - Queue size before: %d\n", timestamp, queueSizeBefore)

	var request EntityCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
		fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities - Invalid JSON: %v\n", time.Now().Format(time.RFC3339), err)
		jsonError(w, http.StatusBadRequest, "Invalid JSON")
		return
	}

	// Log request details
	fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities - Key: %s, ContentType: %s, OwnerAddress: %s, ExpiresIn: %d, PayloadSize: %d\n",
		timestamp, request.Key, request.ContentType, request.OwnerAddress, request.ExpiresIn, len(request.Payload))

	if len(request.StringAnnotations) > 0 {
		fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities - String annotations: %v\n", timestamp, request.StringAnnotations)
	}
	if len(request.NumericAnnotations) > 0 {
		fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities - Numeric annotations: %v\n", timestamp, request.NumericAnnotations)
	}

	// Validate required fields
	if request.Key == "" || request.ContentType == "" || request.OwnerAddress == "" {
		fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities - Missing required fields\n", time.Now().Format(time.RFC3339))
		jsonError(w, http.StatusBadRequest, "Missing required fields: key, contentType, ownerAddress")
		return
	}

	if request.ExpiresIn <= 0 {
		fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities - Invalid ExpiresIn: %d\n", time.Now().Format(time.RFC3339), request.ExpiresIn)
		jsonError(w, http.StatusBadRequest, "expiresIn must be a positive number")
		return
	}

	// Enqueue the entity
	id := writeQueue.EnqueueCreate(&request)
	queueSizeAfter := writeQueue.GetQueueSize()

	fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities - Entity enqueued with ID: %s, Queue size after: %d (delta: %d)\n",
		time.Now().Format(time.RFC3339), id, queueSizeAfter, queueSizeAfter-queueSizeBefore)

	response := map[string]interface{}{
		"success":   true,
		"id":        id,
		"message":   "Entity queued for processing",
		"queueSize": queueSizeAfter,
	}

	jsonResponse(w, http.StatusAccepted, response)
}

// updateEntityHandler handles entity update requests by key.
// Updates are enqueued as OPUpdate operations (and will be emitted after creates in the same block).
func updateEntityHandler(w http.ResponseWriter, r *http.Request) {
	timestamp := time.Now().Format(time.RFC3339)
	vars := mux.Vars(r)
	key := vars["key"]

	queueSizeBefore := writeQueue.GetQueueSize()
	fmt.Printf("[%s] [DEBUG] [HTTP] %s /entities/{key} - Key: %s - Queue size before: %d\n",
		timestamp, r.Method, key, queueSizeBefore)

	if key == "" {
		fmt.Printf("[%s] [DEBUG] [HTTP] %s /entities/{key} - Missing key parameter\n",
			time.Now().Format(time.RFC3339), r.Method)
		jsonError(w, http.StatusBadRequest, "Key parameter is required")
		return
	}

	var request EntityUpdateRequest
	if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
		fmt.Printf("[%s] [DEBUG] [HTTP] %s /entities/{key} - Invalid JSON: %v\n",
			time.Now().Format(time.RFC3339), r.Method, err)
		jsonError(w, http.StatusBadRequest, "Invalid JSON")
		return
	}

	// Key comes from the URL and overrides any body key.
	request.Key = key

	// Validate required fields (same as create).
	if request.ContentType == "" || request.OwnerAddress == "" {
		jsonError(w, http.StatusBadRequest, "Missing required fields: contentType, ownerAddress")
		return
	}

	if request.ExpiresIn <= 0 {
		jsonError(w, http.StatusBadRequest, "expiresIn must be a positive number")
		return
	}

	// Enqueue as an UPDATE operation.
	id := writeQueue.EnqueueUpdate(&request)
	queueSizeAfter := writeQueue.GetQueueSize()

	fmt.Printf("[%s] [DEBUG] [HTTP] %s /entities/{key} - Entity enqueued with ID: %s, Queue size after: %d (delta: %d)\n",
		time.Now().Format(time.RFC3339), r.Method, id, queueSizeAfter, queueSizeAfter-queueSizeBefore)

	response := map[string]interface{}{
		"success":   true,
		"id":        id,
		"message":   "Entity queued for processing",
		"queueSize": queueSizeAfter,
	}

	jsonResponse(w, http.StatusAccepted, response)
}

// getEntityHandler handles get entity by key requests
func getEntityHandler(w http.ResponseWriter, r *http.Request) {
	timestamp := time.Now().Format(time.RFC3339)
	vars := mux.Vars(r)
	key := vars["key"]

	fmt.Printf("[%s] [DEBUG] [HTTP] GET /entities/{key} - Key: %s\n", timestamp, key)

	if key == "" {
		fmt.Printf("[%s] [DEBUG] [HTTP] GET /entities/{key} - Missing key parameter\n", time.Now().Format(time.RFC3339))
		jsonError(w, http.StatusBadRequest, "Key parameter is required")
		return
	}

	entity, err := GetEntityByKey(key)
	if err != nil {
		fmt.Printf("[%s] [DEBUG] [HTTP] GET /entities/{key} - Error: %v\n", time.Now().Format(time.RFC3339), err)
		jsonError(w, http.StatusInternalServerError, "Internal server error")
		return
	}

	if entity == nil {
		fmt.Printf("[%s] [DEBUG] [HTTP] GET /entities/{key} - Entity not found\n", time.Now().Format(time.RFC3339))
		jsonError(w, http.StatusNotFound, "Entity not found")
		return
	}

	fmt.Printf("[%s] [DEBUG] [HTTP] GET /entities/{key} - Entity found: ContentType=%s, PayloadSize=%d, StringAttrs=%d, NumericAttrs=%d\n",
		time.Now().Format(time.RFC3339), entity.ContentType, len(entity.Payload), len(entity.StringAnnotations), len(entity.NumericAnnotations))

	// Convert payload to base64 string
	response := entityToResponse(entity)
	jsonResponse(w, http.StatusOK, response)
}

// queryEntitiesHandler handles query entity requests
func queryEntitiesHandler(w http.ResponseWriter, r *http.Request) {
	timestamp := time.Now().Format(time.RFC3339)
	fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities/query\n", timestamp)

	var request EntityQueryRequest
	if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
		fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities/query - Invalid JSON: %v\n", time.Now().Format(time.RFC3339), err)
		jsonError(w, http.StatusBadRequest, "Invalid JSON")
		return
	}

	limit := request.Limit
	if limit == 0 {
		limit = 100
	}

	fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities/query - OwnerAddress=%s, Limit=%d, Offset=%d, StringAttrs=%d, NumericAttrs=%d\n",
		timestamp, request.OwnerAddress, limit, request.Offset, len(request.StringAnnotations), len(request.NumericAnnotations))

	entities, err := QueryEntities(
		request.OwnerAddress,
		request.StringAnnotations,
		request.NumericAnnotations,
		limit,
		request.Offset,
	)
	if err != nil {
		fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities/query - Error: %v\n", time.Now().Format(time.RFC3339), err)
		jsonError(w, http.StatusInternalServerError, "Internal server error")
		return
	}

	fmt.Printf("[%s] [DEBUG] [HTTP] POST /entities/query - Found %d entities\n", time.Now().Format(time.RFC3339), len(entities))

	// Convert entities to response format
	responseEntities := make([]map[string]interface{}, len(entities))
	for i, entity := range entities {
		responseEntities[i] = entityToResponse(entity)
	}

	response := map[string]interface{}{
		"entities": responseEntities,
		"count":    len(responseEntities),
	}

	jsonResponse(w, http.StatusOK, response)
}

// countEntitiesHandler handles count entities requests
func countEntitiesHandler(w http.ResponseWriter, r *http.Request) {
	timestamp := time.Now().Format(time.RFC3339)
	fmt.Printf("[%s] [DEBUG] [HTTP] GET /entities/count\n", timestamp)

	count, err := CountEntities()
	if err != nil {
		fmt.Printf("[%s] [DEBUG] [HTTP] GET /entities/count - Error: %v\n", time.Now().Format(time.RFC3339), err)
		jsonError(w, http.StatusInternalServerError, "Internal server error")
		return
	}

	fmt.Printf("[%s] [DEBUG] [HTTP] GET /entities/count - Count: %d\n", time.Now().Format(time.RFC3339), count)

	response := map[string]interface{}{
		"count": count,
	}

	jsonResponse(w, http.StatusOK, response)
}

// cleanAllDataHandler handles clean all data requests
func cleanAllDataHandler(w http.ResponseWriter, r *http.Request) {
	timestamp := time.Now().Format(time.RFC3339)
	fmt.Printf("[%s] [DEBUG] [HTTP] DELETE /entities/clean\n", timestamp)

	if err := CleanAllData(); err != nil {
		fmt.Printf("[%s] [DEBUG] [HTTP] DELETE /entities/clean - Error: %v\n", time.Now().Format(time.RFC3339), err)
		jsonError(w, http.StatusInternalServerError, "Internal server error")
		return
	}

	fmt.Printf("[%s] [DEBUG] [HTTP] DELETE /entities/clean - Success\n", time.Now().Format(time.RFC3339))

	response := map[string]interface{}{
		"success": true,
		"message": "All data cleaned",
	}

	jsonResponse(w, http.StatusOK, response)
}

// getReceiptHandler handles get receipt requests
func getReceiptHandler(w http.ResponseWriter, r *http.Request) {
	timestamp := time.Now().Format(time.RFC3339)
	vars := mux.Vars(r)
	id := vars["id"]

	fmt.Printf("[%s] [DEBUG] [HTTP] GET /receipt/{id} - ID: %s\n", timestamp, id)

	if id == "" {
		fmt.Printf("[%s] [DEBUG] [HTTP] GET /receipt/{id} - Missing ID parameter\n", time.Now().Format(time.RFC3339))
		jsonError(w, http.StatusBadRequest, "ID parameter is required")
		return
	}

	// Note: Receipt functionality would need to be implemented in the store
	// For now, return a placeholder response
	fmt.Printf("[%s] [DEBUG] [HTTP] GET /receipt/{id} - Not implemented\n", time.Now().Format(time.RFC3339))
	jsonError(w, http.StatusNotImplemented, "Receipt functionality not yet implemented")
}

// entityToResponse converts an Entity to a response map
func entityToResponse(entity *Entity) map[string]interface{} {
	response := map[string]interface{}{
		"key":                         entity.Key,
		"expiresAt":                   entity.ExpiresAt,
		"contentType":                 entity.ContentType,
		"createdAtBlock":              entity.CreatedAtBlock,
		"lastModifiedAtBlock":         entity.LastModifiedAtBlock,
		"deleted":                     entity.Deleted,
		"transactionIndexInBlock":     entity.TransactionIndexInBlock,
		"operationIndexInTransaction": entity.OperationIndexInTransaction,
		"ownerAddress":                entity.OwnerAddress,
	}

	// Convert payload to base64
	if len(entity.Payload) > 0 {
		response["payload"] = encodeBase64Payload(entity.Payload)
	}

	// Add annotations if present
	if len(entity.StringAnnotations) > 0 {
		response["stringAnnotations"] = entity.StringAnnotations
	}

	if len(entity.NumericAnnotations) > 0 {
		response["numericAnnotations"] = entity.NumericAnnotations
	}

	return response
}

// jsonResponse sends a JSON response
func jsonResponse(w http.ResponseWriter, statusCode int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	json.NewEncoder(w).Encode(data)
}

// jsonError sends a JSON error response
func jsonError(w http.ResponseWriter, statusCode int, message string) {
	jsonResponse(w, statusCode, map[string]string{"error": message})
}

// setupGracefulShutdown sets up signal handlers for graceful shutdown
func setupGracefulShutdown() {
	// Handle SIGINT and SIGTERM
	// Note: This is a simplified version. In production, use signal.Notify
	go func() {
		// This would be handled by the main function or a signal handler
	}()
}

// parseDbPath parses database path from command line arguments
func parseDbPath() string {
	args := os.Args[1:]
	for i, arg := range args {
		if arg == "--db-path" && i+1 < len(args) {
			return args[i+1]
		}
		if len(arg) > 10 && arg[:10] == "--db-path=" {
			return arg[10:]
		}
	}
	return "op-geth-sim.db"
}

// parseTestName parses test name from command line arguments
func parseTestName() string {
	args := os.Args[1:]
	for i, arg := range args {
		if arg == "--testname" && i+1 < len(args) {
			return args[i+1]
		}
		if len(arg) > 10 && arg[:10] == "--testname=" {
			return arg[10:]
		}
	}
	return ""
}

// parsePort parses port from environment variable or uses default
func parsePort() int {
	portStr := os.Getenv("PORT")
	if portStr == "" {
		return 3000
	}
	port, err := strconv.Atoi(portStr)
	if err != nil {
		return 3000
	}
	return port
}
