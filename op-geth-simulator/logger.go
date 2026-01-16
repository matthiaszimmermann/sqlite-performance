package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"time"
)

const (
	logFile           = "performance.log"
	queryLogFile      = "query.log"
	processingLogFile = "processing.log"
)

var currentTestName string

// SetTestName sets the current test name for logging
func SetTestName(testName string) {
	currentTestName = testName
}

// GetTestName returns the current test name
func GetTestName() string {
	return currentTestName
}

// getDefaultTestName generates a default test name based on current time
func getDefaultTestName() string {
	now := time.Now()
	date := now.Format("20060102")
	hours := fmt.Sprintf("%02d", now.Hour())
	minutes := fmt.Sprintf("%02d", now.Minute())
	return fmt.Sprintf("perf_test_%s_%s%s", date, hours, minutes)
}

// logToFile appends a message to a log file
func logToFile(filename, message string) {
	f, err := os.OpenFile(filename, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return
	}
	defer f.Close()

	timestamp := time.Now().Format(time.RFC3339)
	logLine := fmt.Sprintf("[%s] %s\n", timestamp, message)
	f.WriteString(logLine)
}

// logQueryWarning logs a slow query warning
func logQueryWarning(operation string, duration time.Duration, params map[string]interface{}) {
	message := fmt.Sprintf("⚠️  SLOW QUERY: %s took %.2fms (threshold: 200ms)", operation, duration.Seconds()*1000)
	fmt.Println(message)
	logToFile(logFile, fmt.Sprintf("[WARNING] %s", message))
}

// logRequestWarning logs a slow request warning
func logRequestWarning(method, path string, duration time.Duration) {
	message := fmt.Sprintf("⚠️  SLOW REQUEST: %s %s took %dms (threshold: 500ms)", method, path, duration.Milliseconds())
	fmt.Println(message)
	logToFile(logFile, fmt.Sprintf("[WARNING] %s", message))
}

// logBlockWarning logs a slow block processing warning
func logBlockWarning(blockNumber int64, entityCount int, duration time.Duration) {
	message := fmt.Sprintf("⚠️  SLOW BLOCK: Block %d processing %d entities took %.2fms (threshold: 1000ms)", blockNumber, entityCount, duration.Seconds()*1000)
	fmt.Println(message)
	logToFile(logFile, fmt.Sprintf("[WARNING] %s", message))
}

// logQuery logs a query to query.log
func logQuery(queryType string, duration time.Duration, params map[string]interface{}) {
	testName := currentTestName
	if testName == "" {
		testName = getDefaultTestName()
	}

	paramCount := len(params)
	jsonParams := fmt.Sprintf("%v", params) // Simplified JSON representation

	logLine := fmt.Sprintf("%s %s %s %d %d %s\n",
		time.Now().Format(time.RFC3339),
		testName,
		queryType,
		int(duration.Milliseconds()),
		paramCount,
		jsonParams,
	)

	logToFile(queryLogFile, logLine)
}

// logDbOperation logs a database operation
func logDbOperation(operation string, duration time.Duration) {
	timestamp := time.Now().Format(time.RFC3339)
	durationMs := duration.Milliseconds()
	message := fmt.Sprintf("[%s] [INFO] [DB] %s - %dms", timestamp, operation, durationMs)
	fmt.Println(message)

	// Warn if any query takes more than 200ms
	if duration > 200*time.Millisecond {
		logQueryWarning(operation, duration, nil)
	}
}

// logToProcessingLog logs to processing.log
func logToProcessingLog(message string) {
	logToFile(processingLogFile, message)
}

// logBlockInfo logs block processing information with consistent format
func logBlockInfo(level, category, message string) {
	timestamp := time.Now().Format(time.RFC3339)
	fmt.Printf("[%s] [%s] [%s] %s\n", timestamp, level, category, message)
}

// logBlockDebug logs debug information for block processing
func logBlockDebug(blockNumber int64, format string, args ...interface{}) {
	message := fmt.Sprintf("Block %d: %s", blockNumber, fmt.Sprintf(format, args...))
	logBlockInfo("DEBUG", "BLOCK", message)
}

// logBlockInfoMsg logs info messages for block processing
func logBlockInfoMsg(blockNumber int64, format string, args ...interface{}) {
	message := fmt.Sprintf("Block %d: %s", blockNumber, fmt.Sprintf(format, args...))
	logBlockInfo("INFO", "BLOCK", message)
}

// CustomSlogHandler is a slog handler that routes logs to files and stdout
type CustomSlogHandler struct {
	level slog.Level
}

// NewCustomSlogHandler creates a new custom slog handler
func NewCustomSlogHandler() *CustomSlogHandler {
	return &CustomSlogHandler{
		level: slog.LevelInfo,
	}
}

// Enabled reports whether the handler handles records at the given level
func (h *CustomSlogHandler) Enabled(ctx context.Context, level slog.Level) bool {
	return level >= h.level
}

// Handle processes the log record
func (h *CustomSlogHandler) Handle(ctx context.Context, r slog.Record) error {
	// Format the log message and collect attributes for batch tracking
	var msg strings.Builder
	var attrs []slog.Attr

	msg.WriteString(r.Time.Format(time.RFC3339))
	msg.WriteString(" [")
	msg.WriteString(r.Level.String())
	msg.WriteString("] ")
	msg.WriteString(r.Message)

	// Collect attributes and add to message
	r.Attrs(func(a slog.Attr) bool {
		attrs = append(attrs, a)
		msg.WriteString(" ")
		msg.WriteString(a.Key)
		msg.WriteString("=")
		msg.WriteString(fmt.Sprintf("%v", a.Value.Any()))
		return true
	})

	message := msg.String()

	// Track batch processing times/counters for derived metrics
	h.trackBatchWriteTime(r.Message, attrs, r.Time)

	// Always print to stdout
	fmt.Println(message)

	// Route to appropriate log file based on level and content
	switch r.Level {
	case slog.LevelError:
		// Errors go to performance.log
		logToFile(logFile, fmt.Sprintf("[ERROR] %s", message))
	case slog.LevelWarn:
		// Warnings go to performance.log
		logToFile(logFile, fmt.Sprintf("[WARNING] %s", message))
	case slog.LevelInfo:
		// Info logs - check if they're query-related or block-related
		lowerMsg := strings.ToLower(r.Message)
		if strings.Contains(lowerMsg, "query") || strings.Contains(lowerMsg, "get") ||
			strings.Contains(lowerMsg, "insert") || strings.Contains(lowerMsg, "count") {
			// Query-related logs go to query.log
			logToFile(queryLogFile, message)
		} else if strings.Contains(lowerMsg, "block") || strings.Contains(lowerMsg, "follow") ||
			strings.Contains(lowerMsg, "process") {
			// Block processing logs go to processing.log
			logToFile(processingLogFile, message)
		} else {
			// Other info logs go to performance.log
			logToFile(logFile, fmt.Sprintf("[INFO] %s", message))
		}
	case slog.LevelDebug:
		// Debug logs go to performance.log
		logToFile(logFile, fmt.Sprintf("[DEBUG] %s", message))
	}

	return nil
}

func appendRawLine(filename, line string) {
	f, err := os.OpenFile(filename, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.WriteString(line + "\n")
}

func attrInt64(attrs []slog.Attr, key string) (int64, bool) {
	for _, a := range attrs {
		if a.Key != key {
			continue
		}

		v := a.Value.Any()
		switch x := v.(type) {
		case int64:
			return x, true
		case int:
			return int64(x), true
		case int32:
			return int64(x), true
		case uint64:
			return int64(x), true
		case uint:
			return int64(x), true
		case uint32:
			return int64(x), true
		case float64:
			return int64(x), true
		case float32:
			return int64(x), true
		case time.Duration:
			return x.Milliseconds(), true
		case string:
			// Try duration first (e.g. "322ms"), then integer.
			if d, err := time.ParseDuration(x); err == nil {
				return d.Milliseconds(), true
			}
			var n int64
			if _, err := fmt.Sscanf(x, "%d", &n); err == nil {
				return n, true
			}
			return 0, false
		default:
			// Try best-effort integer scan of fmt output.
			var n int64
			if _, err := fmt.Sscanf(fmt.Sprintf("%v", v), "%d", &n); err == nil {
				return n, true
			}
			return 0, false
		}
	}
	return 0, false
}

// trackBatchWriteTime emits a derived metric line for "batch processed" logs:
// [timestamp] <testname> BLOCK-BATCH <start> <end> <creates> <updates> <deletes> <extends> <ownerChanges> <totalOps> <processingTime>
func (h *CustomSlogHandler) trackBatchWriteTime(message string, attrs []slog.Attr, logTime time.Time) {
	lower := strings.ToLower(message)
	if !strings.Contains(lower, "batch processed") {
		return
	}

	// Get test name
	testName := currentTestName
	if testName == "" {
		testName = getDefaultTestName()
	}

	firstBlock, _ := attrInt64(attrs, "firstBlock")
	lastBlock, _ := attrInt64(attrs, "lastBlock")
	processingTime, _ := attrInt64(attrs, "processingTime")
	creates, _ := attrInt64(attrs, "creates")
	updates, _ := attrInt64(attrs, "updates")
	deletes, _ := attrInt64(attrs, "deletes")
	extends, _ := attrInt64(attrs, "extends")
	ownerChanges, _ := attrInt64(attrs, "ownerChanges")
	totalOps := creates + updates + deletes + extends + ownerChanges

	timestamp := logTime.Format(time.RFC3339)
	line := fmt.Sprintf("[%s] %s BLOCK-BATCH %d %d %d %d %d %d %d %d %d",
		timestamp,
		testName,
		firstBlock,
		lastBlock,
		creates,
		updates,
		deletes,
		extends,
		ownerChanges,
		totalOps,
		processingTime,
	)

	// Emit to stdout and append to processing.log (raw, without adding another timestamp prefix)
	fmt.Println(line)
	appendRawLine(processingLogFile, line)
}

// WithAttrs returns a new handler with the given attributes
func (h *CustomSlogHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	// For simplicity, return the same handler
	// In a more complex implementation, you might want to store attrs
	return h
}

// WithGroup returns a new handler with the given group
func (h *CustomSlogHandler) WithGroup(name string) slog.Handler {
	// For simplicity, return the same handler
	return h
}

// GetStoreLogger returns a slog.Logger configured for the sqlite-bitmap-store
// It uses the custom handler to route logs to appropriate files
func GetStoreLogger() *slog.Logger {
	handler := NewCustomSlogHandler()
	return slog.New(handler)
}
