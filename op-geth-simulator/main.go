package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strconv"
	"syscall"
)

func main() {
	// Check if running CLI mode
	if len(os.Args) > 1 && os.Args[1] == "cli" {
		RunCLI()
		return
	}

	// Check if running block replicator mode
	if len(os.Args) > 1 && os.Args[1] == "replicate" {
		RunBlockReplicatorCLI()
		return
	}

	// Parse command line flags
	dbPath := flag.String("db-path", "op-geth-sim.db", "Database file path")
	testName := flag.String("testname", "", "Test name for logging")
	port := flag.Int("port", 3000, "Server port")
	flag.Parse()

	// Override port from environment if set
	if envPort := os.Getenv("PORT"); envPort != "" {
		if p, err := strconv.Atoi(envPort); err == nil {
			port = &p
		}
	}

	// Setup graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		<-sigChan
		fmt.Println("\nShutting down...")
		StopBlockProcessor()
		CloseStore()
		os.Exit(0)
	}()

	// Start server
	if err := StartServer(*port, *dbPath, *testName); err != nil {
		log.Fatalf("Failed to start server: %v", err)
	}
}

