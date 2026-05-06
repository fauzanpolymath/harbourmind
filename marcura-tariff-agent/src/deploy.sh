#!/bin/bash

################################################################################
# HarbourMind Cloud Run Deployment Script
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - Docker installed
#   - GCP project with Cloud Run enabled
#   - Service account with necessary permissions
#
# Usage:
#   ./src/deploy.sh [PROJECT_ID] [REGION]
#
# Example:
#   ./src/deploy.sh my-gcp-project us-central1
################################################################################

set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
PROJECT_ID="${1:-your-gcp-project-id}"
REGION="${2:-us-central1}"
SERVICE_NAME="harbourmind"
IMAGE_NAME="harbourmind"
GCS_BUCKET="${SERVICE_NAME}-logs"

echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}HarbourMind Cloud Run Deployment${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "Project ID:     ${PROJECT_ID}"
echo "Region:         ${REGION}"
echo "Service Name:   ${SERVICE_NAME}"
echo "GCS Bucket:     ${GCS_BUCKET}"
echo ""

# Step 1: Verify gcloud authentication
echo -e "${YELLOW}[1/6]${NC} Verifying gcloud authentication..."
if ! gcloud auth list | grep -q "ACTIVE"; then
    echo -e "${RED}ERROR: Not authenticated with gcloud. Run: gcloud auth login${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Authenticated${NC}"
echo ""

# Step 2: Set GCP project
echo -e "${YELLOW}[2/6]${NC} Setting GCP project..."
gcloud config set project ${PROJECT_ID}
echo -e "${GREEN}✓ Project set${NC}"
echo ""

# Step 3: Build Docker image
echo -e "${YELLOW}[3/6]${NC} Building Docker image..."
IMAGE_URI="gcr.io/${PROJECT_ID}/${IMAGE_NAME}:latest"
docker build -t ${IMAGE_URI} .
echo -e "${GREEN}✓ Image built: ${IMAGE_URI}${NC}"
echo ""

# Step 4: Push to Container Registry
echo -e "${YELLOW}[4/6]${NC} Pushing image to Google Container Registry..."
docker push ${IMAGE_URI}
echo -e "${GREEN}✓ Image pushed${NC}"
echo ""

# Step 5: Create GCS bucket (if doesn't exist)
echo -e "${YELLOW}[5/6]${NC} Setting up Google Cloud Storage bucket..."
if ! gsutil ls -b gs://${GCS_BUCKET} >/dev/null 2>&1; then
    echo "Creating bucket: gs://${GCS_BUCKET}"
    gsutil mb -l ${REGION} gs://${GCS_BUCKET}
    echo -e "${GREEN}✓ Bucket created${NC}"
else
    echo -e "${GREEN}✓ Bucket exists${NC}"
fi
echo ""

# Step 6: Deploy to Cloud Run
echo -e "${YELLOW}[6/6]${NC} Deploying to Cloud Run..."
gcloud run deploy ${SERVICE_NAME} \
    --image ${IMAGE_URI} \
    --platform managed \
    --region ${REGION} \
    --allow-unauthenticated \
    --set-env-vars GCP_PROJECT_ID=${PROJECT_ID},GCS_BUCKET_NAME=${GCS_BUCKET},CORS_ORIGINS="*" \
    --memory 2Gi \
    --timeout 540 \
    --max-instances 100

echo ""
echo -e "${GREEN}✓ Deployment complete${NC}"
echo ""

# Get service URL
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} \
    --platform managed \
    --region ${REGION} \
    --format='value(status.url)')

echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}Service deployed successfully!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "Service URL:    ${SERVICE_URL}"
echo "API Endpoint:   ${SERVICE_URL}/api/v1/"
echo "Health Check:   ${SERVICE_URL}/health"
echo "Docs:           ${SERVICE_URL}/docs"
echo ""
echo "Next steps:"
echo "  1. Visit ${SERVICE_URL} to see the landing page"
echo "  2. Upload PDFs to test the calculation endpoint"
echo "  3. Check logs in Cloud Logging dashboard"
echo "  4. Monitor storage in gs://${GCS_BUCKET}"
echo ""
