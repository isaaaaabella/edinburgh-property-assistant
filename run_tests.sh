#!/usr/bin/env bash
# Run the full pytest suite.
#
# Usage:
#   ./run_tests.sh                       # standard run
#   NOTION_PARITY_TEST=1 ./run_tests.sh  # also include live Notion round-trip
#                                          (creates + archives a test page)
set -e

cd "$(dirname "$0")"
# Run from parent so `property_assistant.<module>` imports resolve
cd ..
python -m pytest property_assistant/tests/ "$@"
