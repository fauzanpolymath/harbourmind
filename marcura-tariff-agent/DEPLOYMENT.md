# HarbourMind Cloud Deployment Guide

## Overview

HarbourMind can be deployed to Google Cloud Run with Cloud Storage integration for logs and automatic scaling capabilities.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Cloud Run Service                     │
│  ┌────────────────────────────────────────────────────┐  │
│  │  FastAPI Application                               │  │
│  │  - Website (index.html)                            │  │
│  │  - API endpoints (/api/v1/*)                       │  │
│  │  - Health checks                                   │  │
│  └────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
         │                                    │
         ▼                                    ▼
    ┌─────────────┐            ┌──────────────────────────┐
    │ Cloud Logs  │            │ Cloud Storage (GCS)      │
    │ - Metrics   │            │ - Calculation logs       │
    │ - Traces    │            │ - PDF files              │
    └─────────────┘            └──────────────────────────┘
```

## Prerequisites

### Local Development

1. **Docker** - For building container images
   ```bash
   docker --version  # Should be 20.10+
   ```

2. **Google Cloud SDK** - For deployment
   ```bash
   curl https://sdk.cloud.google.com | bash
   exec -l $SHELL
   gcloud --version
   ```

3. **Google Cloud Project** - With billing enabled
   ```bash
   gcloud projects list
   ```

### GCP Setup

1. **Enable APIs**
   ```bash
   gcloud services enable \
     run.googleapis.com \
     storage.googleapis.com \
     logging.googleapis.com \
     containerregistry.googleapis.com
   ```

2. **Set Default Project**
   ```bash
   gcloud config set project YOUR_PROJECT_ID
   ```

3. **Authenticate with Docker**
   ```bash
   gcloud auth configure-docker
   ```

## Deployment Steps

### Option 1: Automated Deployment Script

```bash
cd marcura-tariff-agent
chmod +x src/deploy.sh
./src/deploy.sh YOUR_PROJECT_ID us-central1
```

The script will:
1. Verify authentication
2. Build Docker image
3. Push to Container Registry
4. Create GCS bucket
5. Deploy to Cloud Run

### Option 2: Manual Deployment

**Step 1: Build Docker Image**
```bash
PROJECT_ID="your-project-id"
docker build -t gcr.io/${PROJECT_ID}/harbourmind:latest .
```

**Step 2: Push to Container Registry**
```bash
docker push gcr.io/${PROJECT_ID}/harbourmind:latest
```

**Step 3: Create GCS Bucket**
```bash
gsutil mb -l us-central1 gs://harbourmind-logs
```

**Step 4: Deploy to Cloud Run**
```bash
gcloud run deploy harbourmind \
  --image gcr.io/${PROJECT_ID}/harbourmind:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars \
    GCP_PROJECT_ID=${PROJECT_ID},\
    GCS_BUCKET_NAME=harbourmind-logs,\
    CORS_ORIGINS="*" \
  --memory 2Gi \
  --timeout 540 \
  --max-instances 100
```

## Configuration

### Environment Variables

Set in Cloud Run:

```env
GCP_PROJECT_ID=your-project-id
GCS_BUCKET_NAME=harbourmind-logs
CORS_ORIGINS=*
APP_ENV=production
LOG_LEVEL=INFO
GOOGLE_API_KEY=your-gemini-api-key
LLAMAPARSE_API_KEY=your-llamaparse-api-key
```

### Service Configuration

**Memory**: 2 GB (recommended for PDF processing)
**Timeout**: 540 seconds (9 minutes for large PDF processing)
**Max Instances**: 100 (auto-scaling limit)
**Min Instances**: 1 (always available)

## Testing Deployment

### 1. Health Check
```bash
SERVICE_URL=$(gcloud run services describe harbourmind \
  --platform managed \
  --region us-central1 \
  --format='value(status.url)')

curl ${SERVICE_URL}/health
```

### 2. API Test
```bash
curl ${SERVICE_URL}/api/v1/logs
```

### 3. Website Test
```bash
# Visit in browser
open ${SERVICE_URL}
```

### 4. File Upload Test
```bash
curl -X POST ${SERVICE_URL}/api/v1/calculate-from-pdfs \
  -F "tariff_pdf=@Port_Tariff.pdf" \
  -F "vessel_pdf=@vessel_cert.pdf"
```

## Monitoring

### View Logs
```bash
gcloud run logs read harbourmind --platform managed --region us-central1
```

### Monitor Performance
```bash
# CPU utilization
gcloud monitoring time-series list \
  --filter='resource.type="cloud_run_revision" AND metric.type="run.googleapis.com/request_count"'
```

### Check GCS Bucket
```bash
gsutil ls -r gs://harbourmind-logs/
```

## Troubleshooting

### Service won't start
```bash
# Check logs
gcloud run logs read harbourmind --limit 100

# Verify image exists
gcloud container images list --repository=gcr.io/${PROJECT_ID}
```

### Out of Memory
Increase memory allocation:
```bash
gcloud run update harbourmind \
  --region us-central1 \
  --memory 4Gi
```

### CORS Errors
Verify CORS_ORIGINS environment variable is set:
```bash
gcloud run services describe harbourmind \
  --region us-central1 \
  --platform managed \
  --format='value(spec.template.spec.containers[0].env)'
```

### PDF Processing Timeout
Increase timeout:
```bash
gcloud run update harbourmind \
  --region us-central1 \
  --timeout 900  # 15 minutes
```

## Cost Optimization

### Recommendations

1. **Set min instances to 0**
   ```bash
   gcloud run update harbourmind \
     --region us-central1 \
     --min-instances 0
   ```

2. **Use Cloud Storage lifecycle policies** (delete old logs)
   ```bash
   # Create lifecycle.json
   gsutil lifecycle set lifecycle.json gs://harbourmind-logs
   ```

3. **Monitor bill**
   ```bash
   gcloud billing budgets list
   ```

## Scaling

### Auto-scaling Configuration

Current defaults:
- Min instances: 1
- Max instances: 100
- Target CPU: 80%
- Target memory: 80%

To modify:
```bash
gcloud run update harbourmind \
  --region us-central1 \
  --min-instances 0 \
  --max-instances 50
```

## Security

### Best Practices

1. **Don't use `--allow-unauthenticated` in production**
   ```bash
   gcloud run update harbourmind \
     --region us-central1 \
     --no-allow-unauthenticated
   ```

2. **Use IAM for access control**
   ```bash
   gcloud run add-iam-policy-binding harbourmind \
     --region us-central1 \
     --member=serviceAccount:your-sa@project.iam.gserviceaccount.com \
     --role=roles/run.invoker
   ```

3. **Enable VPC Connector** (for private database access)
   ```bash
   gcloud run update harbourmind \
     --region us-central1 \
     --vpc-connector=harbourmind-connector
   ```

4. **Rotate API keys regularly**
   - Update GOOGLE_API_KEY and LLAMAPARSE_API_KEY

## Rollback

### Previous Version
```bash
# List revisions
gcloud run revisions list --service harbourmind --region us-central1

# Roll back to previous revision
gcloud run update harbourmind \
  --region us-central1 \
  --revision=harbourmind-00002
```

## Alternative Deployment: Firebase Hosting

For serving the static website separately:

```bash
# Deploy website to Firebase Hosting
firebase init hosting
firebase deploy --only hosting

# Keep API on Cloud Run
# Update website API calls to: https://cloud-run-url/api/v1
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Deploy to Cloud Run

on:
  push:
    branches: [main]

env:
  PROJECT_ID: ${{ secrets.GCP_PROJECT_ID }}
  REGION: us-central1
  SERVICE_NAME: harbourmind

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Setup Cloud SDK
        uses: google-github-actions/setup-gcloud@v0
        with:
          project_id: ${{ secrets.GCP_PROJECT_ID }}
          service_account_key: ${{ secrets.GCP_SA_KEY }}
          export_default_credentials: true
      
      - name: Build and Push
        run: |
          docker build -t gcr.io/$PROJECT_ID/harbourmind:$GITHUB_SHA .
          docker push gcr.io/$PROJECT_ID/harbourmind:$GITHUB_SHA
      
      - name: Deploy
        run: |
          gcloud run deploy $SERVICE_NAME \
            --image gcr.io/$PROJECT_ID/harbourmind:$GITHUB_SHA \
            --region $REGION \
            --platform managed
```

## Support & Troubleshooting

For additional help:

1. **Cloud Run Documentation**
   https://cloud.google.com/run/docs

2. **GCP Console**
   https://console.cloud.google.com

3. **Cloud Monitoring Dashboard**
   https://console.cloud.google.com/monitoring

4. **GitHub Issues**
   https://github.com/fauzanpolymath/harbourmind/issues
