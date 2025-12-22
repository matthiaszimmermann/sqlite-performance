#!/bin/bash

# Script to convert processing.log to CSV format
# Usage: ./process_log_to_csv.sh [testname] [output.csv]
#   If testname is provided, only processes logs for that testname
#   If output.csv is not provided, defaults to processing.csv

LOG_FILE="${LOG_FILE:-processing.log}"
TESTNAME="${1:-}"
OUTPUT_FILE="${2:-processing.csv}"

# Check if log file exists
if [ ! -f "$LOG_FILE" ]; then
    echo "Error: Log file '$LOG_FILE' not found" >&2
    exit 1
fi

# Create CSV with header
echo "testname,block_number,insert_time_ms,remove_time_ms,total_time_ms,num_entities,num_string_attributes,num_numeric_attributes" > "$OUTPUT_FILE"

# Process log file
if [ -z "$TESTNAME" ]; then
    # Process all testnames - filter for BLOCK lines only
    grep " BLOCK " "$LOG_FILE" | awk '{
        testname = $1
        block_num = $3
        insert_time = $4
        remove_time = $5
        total_time = $6
        num_entities = $7
        str_attrs = $8
        num_attrs = $9
        print testname "," block_num "," insert_time "," remove_time "," total_time "," num_entities "," str_attrs "," num_attrs
    }' >> "$OUTPUT_FILE"
else
    # Process only specified testname - filter for BLOCK lines only
    grep "^${TESTNAME} BLOCK " "$LOG_FILE" | awk '{
        testname = $1
        block_num = $3
        insert_time = $4
        remove_time = $5
        total_time = $6
        num_entities = $7
        str_attrs = $8
        num_attrs = $9
        print testname "," block_num "," insert_time "," remove_time "," total_time "," num_entities "," str_attrs "," num_attrs
    }' >> "$OUTPUT_FILE"
fi

echo "CSV file created: $OUTPUT_FILE"
if [ -n "$TESTNAME" ]; then
    echo "Filtered by testname: $TESTNAME"
fi

