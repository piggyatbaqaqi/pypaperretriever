#!/bin/bash
# Build pypaperretriever wheel with auto-incrementing build number.
#
# The base version in setup.py (e.g. 1.0.0) is combined with a build
# number to produce a PEP 440 version like 1.0.0.post42.
#
# Usage:
#   ./build-wheel.sh

set -e

cd "$(dirname "$0")"

BUILD_NUMBER_FILE=".build-number"
if [ -f "$BUILD_NUMBER_FILE" ]; then
    BUILD_NUMBER=$(cat "$BUILD_NUMBER_FILE")
else
    BUILD_NUMBER=0
fi
BUILD_NUMBER=$((BUILD_NUMBER + 1))
echo "$BUILD_NUMBER" > "$BUILD_NUMBER_FILE"

# Extract base version from setup.py
BASE_VERSION=$(python3 -c "
import re, sys
with open('setup.py') as f:
    m = re.search(r\"version='([^']+)'\", f.read())
if m:
    print(m.group(1))
else:
    sys.exit(1)
")

FULL_VERSION="${BASE_VERSION}.post${BUILD_NUMBER}"
echo "=== Building pypaperretriever wheel (${FULL_VERSION}) ==="

# Clean previous builds
rm -rf dist/ build/ *.egg-info

# Inject versioned setup.py
sed -i "s/version='${BASE_VERSION}'/version='${FULL_VERSION}'/" setup.py

# Build the wheel
python3 -m build --wheel --outdir dist/

# Restore setup.py to base version
sed -i "s/version='${FULL_VERSION}'/version='${BASE_VERSION}'/" setup.py

echo "=== Done ==="
ls -la dist/*.whl
