#!/bin/bash
#Run ALKiln tests for all production interviews on a given server
#Usage: bash tests/test_interviews.sh <server_url> [github_org]
#Example: bash tests/test_interviews.sh https://apps-dev.suffolklitlab.org SuffolkLITLab

if [ -z "$1" ]; then
  echo "Usage: test_interviews.sh <server_url> [github_org]"
  echo "Example: bash tests/test_interviews.sh https://apps-dev.suffolklitlab.org SuffolkLITLab"
  exit 1
fi

SERVER=$1
GITHUB_ORG=${2:-"SuffolkLITLab"}

echo "Fetching interview list from $SERVER..."

INTERVIEWS=$(curl -s "$SERVER/list?json=1")

if [ -z "$INTERVIEWS" ]; then
  echo "ERROR: Could not fetch interview list from $SERVER"
  exit 1
fi

echo "Getting interviews..."

PACKAGES=$(echo "$INTERVIEWS" | python3 -c "
import sys, json
data = json.load(sys.stdin)
interviews = data.get('interviews', [])
for i in interviews:
    maturity = i.get('metadata', {}).get('maturity', '')
    package = i.get('package', '')
    filename = i.get('filename', '')
    if maturity == 'production' and package.startswith('docassemble.'):
        print(f'{package}|{filename}')
" | grep "${3:-}")

if [ -z "$PACKAGES" ]; then
  echo "ERROR: No interviews found"
  exit 1
fi

echo ""
echo "Checking GitHub repos..."

VALID_PACKAGES=""
while IFS='|' read -r package filename; do
  repo_name=$(echo "$package" | sed 's/docassemble\./docassemble-/')
  github_url="https://github.com/${GITHUB_ORG}/${repo_name}"
  code=$(curl -s -o /dev/null -w "%{http_code}" "$github_url")
  if [ "$code" = "200" ]; then
    echo "  [FOUND] $package"
    VALID_PACKAGES="${VALID_PACKAGES}${package}|${filename}|${github_url}"$'\n'
  else
    echo "  [MISSING] $package (not in ${GITHUB_ORG} org, skipping)"
  fi
done <<< "$PACKAGES"

if [ -z "$VALID_PACKAGES" ]; then
  echo "ERROR: No valid ${GITHUB_ORG} interviews found"
  exit 1
fi

IMAGE="ghcr.io/suffolklitlab/docassemble:latest"
TEST_CONTAINER="interview-test-$$"
TEST_PORT="8082"

cleanup() {
  echo ""
  echo "Cleaning up test container..."
  docker stop "$TEST_CONTAINER" 2>/dev/null || true
  docker rm "$TEST_CONTAINER" 2>/dev/null || true
  rm -rf "$STATE_DIR"
}
trap cleanup EXIT

echo ""
echo "Pulling latest Docassemble image..."
docker pull "$IMAGE"

echo ""
DA_ADMIN_EMAIL="admin@example.com"
DA_ADMIN_PASSWORD="@123abcdefg"
DA_ADMIN_API_KEY="abcd1234abcd1234abcd5678abdc5678"

docker run -d \
  --name "$TEST_CONTAINER" \
  -p "${TEST_PORT}:80" \
  --env DA_ADMIN_EMAIL="$DA_ADMIN_EMAIL" \
  --env DA_ADMIN_PASSWORD="$DA_ADMIN_PASSWORD" \
  --env DA_ADMIN_API_KEY="$DA_ADMIN_API_KEY" \
  --cap-add SYS_PTRACE \
  "$IMAGE"

echo "Waiting for Docassemble to boot..."
sleep 60
until curl -s -o /dev/null -w "%{http_code}" "http://localhost:${TEST_PORT}/health_check?ready=1" | grep -q "200"; do
  echo "  still waiting..."
  sleep 15
done
echo "Docassemble is up."

echo ""
echo "Installing docassemblecli..."
pip install "docassemblecli==0.0.25" > /dev/null 2>&1
echo "docassemblecli installed."
echo "Installing ALKiln..."
npm install -g @suffolklitlab/alkiln@v5 > /dev/null 2>&1
echo "ALKiln installed."

echo ""
echo "Phase 1: Installing all packages..."
echo ""
rm -rf alkiln-results/
mkdir -p alkiln-results
PASS_LIST=""
FAIL_LIST=""

#Store temp dirs and branches so we can reuse them in the test phase
STATE_DIR=$(mktemp -d)
INSTALL_FAILED=""

while IFS='|' read -r package filename github_url; do
  [ -z "$package" ] && continue
  echo "--- $package ---"

  TEMP_DIR=$(mktemp -d)

  if ! git clone --depth 1 "$github_url" "$TEMP_DIR" 2>&1; then
    echo "  [CLONE FAILED] $package"
    INSTALL_FAILED="${INSTALL_FAILED}${package}"$'\n'
    rm -rf "$TEMP_DIR"
    continue
  fi

  DEFAULT_BRANCH=$(git -C "$TEMP_DIR" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')
  [ -z "$DEFAULT_BRANCH" ] && DEFAULT_BRANCH="master"

  if ! dainstall "$TEMP_DIR" \
                 --apiurl "http://localhost:${TEST_PORT}" \
                 --apikey "$DA_ADMIN_API_KEY" 2>&1; then
    echo "  [INSTALL FAILED] $package"
    INSTALL_FAILED="${INSTALL_FAILED}${package}"$'\n'
    rm -rf "$TEMP_DIR"
    continue
  fi

  echo "  [INSTALLED] $package"
  SAFE_NAME=$(echo "$package" | tr '.' '_')
  echo "$TEMP_DIR" > "$STATE_DIR/${SAFE_NAME}.dir"
  echo "$DEFAULT_BRANCH" > "$STATE_DIR/${SAFE_NAME}.branch"

done <<< "$VALID_PACKAGES"


echo ""
echo "Waiting for server to stabilize after all installs..."
until curl -s -o /dev/null -w "%{http_code}" "http://localhost:${TEST_PORT}/health_check?ready=1" | grep -q "200"; do
  echo "  still waiting..."
  sleep 15
done
echo "Server ready."

echo ""
echo "Phase 2: Running tests..."
echo ""

PASS=0
FAIL=0

while IFS='|' read -r package filename github_url; do
  [ -z "$package" ] && continue

  # Skip packages that failed to install
  if echo "$INSTALL_FAILED" | grep -qx "$package"; then
    echo "  [SKIP] $package (install failed)"
    FAIL=$((FAIL + 1))
    continue
  fi

  echo "--- $package ---"
  SAFE_NAME=$(echo "$package" | tr '.' '_')
  TEMP_DIR=$(cat "$STATE_DIR/${SAFE_NAME}.dir" 2>/dev/null)
  DEFAULT_BRANCH=$(cat "$STATE_DIR/${SAFE_NAME}.branch" 2>/dev/null)

  #generate a fallback test if no feature files exist
  COUNT=$(find "$TEMP_DIR" -name "*.feature" | wc -l)
  if [ "$COUNT" -eq 0 ]; then
    echo "  No tests found, generating fallback load test..."
    mkdir -p "$TEMP_DIR/docassemble/${package#docassemble.}/data/sources"
    INTERVIEW_NAME="${filename#*:data/questions/}"
    INTERVIEW_NAME="${INTERVIEW_NAME%.yml}"
    cat > "$TEMP_DIR/docassemble/${package#docassemble.}/data/sources/interview_loads.feature" << EOF
Feature: Interview loads
  Scenario: Interview loads
    Given the max seconds for each step in this scenario is 60
    And I start the interview at "${INTERVIEW_NAME}"
EOF
  fi

  echo "  Running tests for $package..."
  cd "$TEMP_DIR"

  SERVER_URL="http://localhost:${TEST_PORT}" \
  DOCASSEMBLE_DEVELOPER_API_KEY="$DA_ADMIN_API_KEY" \
  REPO_URL="$github_url" \
  BRANCH_NAME="$DEFAULT_BRANCH" \
  _ORIGIN="local" \
  alkiln-server-install 2>&1

  if SERVER_URL="http://localhost:${TEST_PORT}" \
     DOCASSEMBLE_DEVELOPER_API_KEY="$DA_ADMIN_API_KEY" \
     REPO_URL="$github_url" \
     BRANCH_NAME="$DEFAULT_BRANCH" \
     _ORIGIN="local" \
     DEBUG="${DEBUG:-}" \
     alkiln-run "not @efile" 2>&1; then
    echo "  [PASS] $package"
    PASS=$((PASS + 1))
    PASS_LIST="${PASS_LIST}  $package"$'\n'
  else
    echo "  [FAIL] $package"
    FAIL=$((FAIL + 1))
    FAIL_LIST="${FAIL_LIST}  $package"$'\n'
  fi

  cd - > /dev/null

  ALKILN_OUTPUT=$(find "$TEMP_DIR" -name "alkiln-*" -type d 2>/dev/null)
  [ -n "$ALKILN_OUTPUT" ] && cp -r "$ALKILN_OUTPUT" "./alkiln-results/${package}"
  rm -rf "$TEMP_DIR"

done <<< "$VALID_PACKAGES"

echo ""
printf "\nResults: %d passed, %d failed\n" "$PASS" "$FAIL"

echo ""
echo "Passed:"
echo "$PASS_LIST" | grep . || echo "  none"
echo ""
echo "Failed:"
echo "$FAIL_LIST" | grep . || echo "  none"