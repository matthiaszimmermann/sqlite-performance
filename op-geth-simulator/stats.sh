#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <test_name> [column] [log_file]"
  echo "Example: $0 perf_test_20260313_1534 12 processing.log"
  exit 1
fi

TEST_NAME="${1}"
COLUMN="${2:-12}"
LOG_FILE="${3:-processing.log}"

cat "$LOG_FILE" | grep "${TEST_NAME} BLOCK-BATCH" | awk "{print \$$COLUMN}" | sort -n | awk '
  BEGIN { count=0 }
  { values[count++] = $1; sum += $1 }
  END {
    if (count == 0) { print "No data found"; exit 1 }
    mean = sum / count
    median = (count % 2 == 1) ? values[int(count/2)] : (values[count/2-1] + values[count/2]) / 2
    p90 = values[int(count * 0.9)]
    p99 = values[int(count * 0.99)]
    print "count:  " count
    print "mean:   " mean
    print "median: " median
    print "p90:    " p90
    print "p99:    " p99
    print "min:    " values[0]
    print "max:    " values[count-1]
  }
'
