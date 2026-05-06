#!/bin/bash
# ────────────────────────────────────────────────────────────────────────────
# HarbourMind Cloud Run Deployment Script
#
# Usage:
#   ./deploy.sh YOUR_PROJECT_ID [REGION] [SERVICE_NAME]
#
# Examples:
#   ./deploy.sh my-gcp-project
#   ./deploy.sh my-gcp-project us-east1
#   ./deploy.sh my-gcp-project us-central1 harbourmind-prod
# ────────────────────────────────────────────────────────────────────────────

set -e  # Exit on error

# ────────────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────────────

PROJECT_ID="${1:-}"
REGION="${2:-us-central1}"
SERVICE_NAME="${3:-harbourmind}"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
MEMORY="2Gi"
TIMEOUT="540"
MAX_INSTANCES="100"
MIN_INSTANCES="1"
PORT="8080"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ────────────────────────────────────────────────────────────────────────────
# Functions
# ────────────────────────────────────────────────────────────────────────────

print_header() {
    echo -e "\n${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}\n"
}

print_step() {
    echo -e "${YELLOW}[$(date +'%H:%M:%S')]${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# ────────────────────────────────────────────────────────────────────────────
# Validation
# ────────────────────────────────────────────────────────────────────────────

if [ -z "$PROJECT_ID" ]; then
    print_error "Project ID not provided"
    echo "Usage: $0 YOUR_PROJECT_ID [REGION] [SERVICE_NAME]"
    echo "Example: $0 my-gcp-project us-central1 harbourmind"
    exit 1
fi

print_header "HarbourMind Cloud Run Deployment"
echo "Project ID:    $PROJECT_ID"
echo "Region:        $REGION"
echo "Service Name:  $SERVICE_NAME"
echo "Image:         $IMAGE_NAME"
echo "Memory:        $MEMORY"
echo "Timeout:       ${TIMEOUT}s"
echo "Port:          $PORT"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# Step 1: Verify gcloud authentication
# ────────────────────────────────────────────────────────────────────────────

print_step "Verifying gcloud authentication..."
if gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q .; then
    print_success "Authentication verified"
else
    print_error "Not authenticated with gcloud"
    echo "Run: gcloud auth login"
    exit 1
fi

# ────────────────────────────────────────────────────────────────────────────
# Step 2: Set GCP project
# ────────────────────────────────────────────────────────────────────────────

print_step "Setting GCP project to $PROJECT_ID..."
gcloud config set project "$PROJECT_ID"
print_success "Project set"

# ────────────────────────────────────────────────────────────────────────────
# Step 3: Enable required APIs
# ────────────────────────────────────────────────────────────────────────────

print_step "Enabling required GCP APIs..."
gcloud services enable \
    run.googleapis.com \
    storage.googleapis.com \
    containerregistry.googleapis.com \
    logging.googleapis.com \
    2>/dev/null || true
print_success "APIs enabled (or already enabled)"

# ────────────────────────────────────────────────────────────────────────────
# Step 4: Configure Docker authentication
# ────────────────────────────────────────────────────────────────────────────

print_step "Configuring Docker authentication for Container Registry..."
gcloud auth configure-docker --quiet
print_success "Docker configured"

# ────────────────────────────────────────────────────────────────────────────
# Step 5: Build Docker image
# ────────────────────────────────────────────────────────────────────────────

print_step "Building Docker image: $IMAGE_NAME..."
docker build -t "$IMAGE_NAME:latest" .
print_success "Docker image built"

# ────────────────────────────────────────────────────────────────────────────
# Step 6: Push to Container Registry
# ────────────────────────────────────────────────────────────────────────────

print_step "Pushing image to Container Registry..."
docker push "$IMAGE_NAME:latest"
print_success "Image pushed to gcr.io"

# ────────────────────────────────────────────────────────────────────────────
# Step 7: Create GCS bucket (if needed)
# ────────────────────────────────────────────────────────────────────────────

BUCKET_NAME="harbourmind-logs-${PROJECT_ID}"
print_step "Checking Cloud Storage bucket: gs://${BUCKET_NAME}..."

if gsutil ls -b "gs://${BUCKET_NAME}" &>/dev/null; then
    print_success "Bucket already exists"
else
    print_step "Creating bucket..."
    gsutil mb -l "$REGION" "gs://${BUCKET_NAME}"
    print_success "Bucket created"
fi

# ────────────────────────────────────────────────────────────────────────────
# Step 8: Deploy to Cloud Run
# ────────────────────────────────────────────────────────────────────────────

print_step "Deploying to Cloud Run..."

gcloud run deploy "$SERVICE_NAME" \
    --image "$IMAGE_NAME:latest" \
    --platform managed \
    --region "$REGION" \
    --allow-unauthenticated \
    --memory "$MEMORY" \
    --timeout "$TIMEOUT" \
    --max-instances "$MAX_INSTANCES" \
    --min-instances "$MIN_INSTANCES" \
    --port "$PORT" \
    --set-env-vars \
        GCP_PROJECT_ID="$PROJECT_ID",\
        GCS_BUCKET_NAME="$BUCKET_NAME",\
        CORS_ORIGINS="*",\
        APP_ENV="production",\
        LOG_LEVEL="INFO" \
    --quiet

print_success "Deployment complete!"

# ────────────────────────────────────────────────────────────────────────────
# Step 9: Get service URL and test
# ────────────────────────────────────────────────────────────────────────────

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
    --platform managed \
    --region "$REGION" \
    --format='value(status.url)')

print_header "Deployment Successful!"

echo "Service URL:  $SERVICE_URL"
echo "Region:       $REGION"
echo "Bucket:       gs://$BUCKET_NAME"
echo ""
echo "Next steps:"
echo ""
echo "  1. Set your API keys in Cloud Run:"
echo "     gcloud run update $SERVICE_NAME --region $REGION \\"
echo "       --set-env-vars GEMINI_API_KEY=your-key,LLAMAPARSE_API_KEY=your-key"
echo ""
echo "  2. Test the service:"
echo "     curl ${SERVICE_URL}/health"
echo "     curl ${SERVICE_URL}/api/v1/logs"
echo ""
echo "  3. View logs:"
echo "     gcloud run logs read $SERVICE_NAME --region $REGION --follow"
echo ""
echo "  4. View Cloud Storage:"
echo "     gsutil ls -r gs://$BUCKET_NAME/"
echo ""
echo "  5. Visit in browser:"
echo "     open $SERVICE_URL"
echo ""
