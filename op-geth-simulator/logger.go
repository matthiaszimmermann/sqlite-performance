package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"regexp"
	"strings"
	"sync"
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
	level           slog.Level
	batchStartTimes map[int64]time.Time // Track when batches start by block number
	batchMutex      sync.Mutex          // Protect batchStartTimes map
}

var (
	// Regex patterns to extract block numbers from log messages
	newBatchRegex     = regexp.MustCompile(`firstBlock=(\d+)`)
	blockUpdatedRegex = regexp.MustCompile(`block=(\d+)`)
)

// NewCustomSlogHandler creates a new custom slog handler
func NewCustomSlogHandler() *CustomSlogHandler {
	return &CustomSlogHandler{
		level:           slog.LevelInfo,
		batchStartTimes: make(map[int64]time.Time),
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

	// Track batch write times - extract block number from attributes
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

// trackBatchWriteTime tracks the time between "new batch" and "block updated" logs
func (h *CustomSlogHandler) trackBatchWriteTime(message string, attrs []slog.Attr, logTime time.Time) {
	h.batchMutex.Lock()
	defer h.batchMutex.Unlock()

	var blockNum int64
	var foundBlockNum bool

	// Extract block number from attributes
	for _, a := range attrs {
		if a.Key == "firstBlock" || a.Key == "block" {
			if intVal, ok := a.Value.Any().(int64); ok {
				blockNum = intVal
				foundBlockNum = true
				break
			} else if uintVal, ok := a.Value.Any().(uint64); ok {
				blockNum = int64(uintVal)
				foundBlockNum = true
				break
			} else if intVal, ok := a.Value.Any().(int); ok {
				blockNum = int64(intVal)
				foundBlockNum = true
				break
			}
		}
	}

	// If block number not found in attributes, try parsing from message
	if !foundBlockNum {
		if strings.Contains(message, "new batch") {
			matches := newBatchRegex.FindStringSubmatch(message)
			if len(matches) >= 2 {
				if _, err := fmt.Sscanf(matches[1], "%d", &blockNum); err == nil {
					foundBlockNum = true
				}
			}
		} else if strings.Contains(message, "block updated") {
			matches := blockUpdatedRegex.FindStringSubmatch(message)
			if len(matches) >= 2 {
				if _, err := fmt.Sscanf(matches[1], "%d", &blockNum); err == nil {
					foundBlockNum = true
				}
			}
		}
	}

	// Check if this is a "new batch" log
	if strings.Contains(message, "new batch") && foundBlockNum {
		h.batchStartTimes[blockNum] = logTime
		return
	}

	// Check if this is a "block updated" log
	if strings.Contains(message, "block updated") && foundBlockNum {
		// Check if we have a start time for this block
		if startTime, exists := h.batchStartTimes[blockNum]; exists {
			duration := logTime.Sub(startTime)
			durationMs := duration.Milliseconds()

			// Get test name
			testName := currentTestName
			if testName == "" {
				testName = getDefaultTestName()
			}

			// Log the block write time measurement
			timestamp := logTime.Format(time.RFC3339)
			writeTimeLog := fmt.Sprintf("[%s] [INFO] [BLOCK] %s Block %d: Write time - %dms", timestamp, testName, blockNum, durationMs)
			fmt.Println(writeTimeLog)

			// Also write to processing.log
			logToFile(processingLogFile, fmt.Sprintf("%s Block %d write time: %dms", testName, blockNum, durationMs))

			// Clean up the start time
			delete(h.batchStartTimes, blockNum)

			// Warn if write time is too long
			if duration > 1000*time.Millisecond {
				logBlockWarning(blockNum, 0, duration)
			}
		} else {
			// If we don't have a start time, log a warning (might have missed the "new batch" log)
			timestamp := logTime.Format(time.RFC3339)
			fmt.Printf("[%s] [WARN] [BLOCK] Block %d: Write time measurement skipped (no start time found)\n", timestamp, blockNum)
		}
		return
	}
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
