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
	message := fmt.Sprintf("[DB] %s - %.2fms", operation, duration.Seconds()*1000)
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
	// Format the log message
	var msg strings.Builder
	msg.WriteString(r.Time.Format(time.RFC3339))
	msg.WriteString(" [")
	msg.WriteString(r.Level.String())
	msg.WriteString("] ")
	msg.WriteString(r.Message)

	// Add attributes
	r.Attrs(func(a slog.Attr) bool {
		msg.WriteString(" ")
		msg.WriteString(a.Key)
		msg.WriteString("=")
		msg.WriteString(fmt.Sprintf("%v", a.Value.Any()))
		return true
	})

	message := msg.String()

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
