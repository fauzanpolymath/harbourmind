# Docker & Google Cloud Integration Guide

## 🐳 Docker Architecture

### Multi-Stage Build Process

```
Dockerfile (3 stages)
│
├─ Stage 1: base
│  └─ FROM python:3.11-slim
│     • Minimal Python image (~180 MB)
│     • Set PYTHONDONTWRITEBYTECODE=1 (no .pyc files)
│     • Set PYTHONUNBUFFERED=1 (real-time logs)
│
├─ Stage 2: dependencies  
│  └─ Install requirements.txt
│     • pip install --no-cache-dir (smaller image)
│     • Upgrade pip, then install packages
│     • Dependencies cached for faster rebuilds
│
└─ Stage 3: runtime
   └─ Copy application code
      • COPY . .
      • EXPOSE 8000
      • CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### Why This Design?

| Benefit | Reason |
|---------|--------|
| **Smaller images** | Each layer only keeps what's needed |
| **Faster rebuilds** | Dependencies cached (layer 2) when code changes (layer 3) |
| **Cleaner separation** | Base → Dependencies → Application |
| **Production-ready** | Uses uvicorn (not `--reload`) |

### Build Command

```bash
# Build locally
docker build -t harbourmind:latest .

# Build and push to Google Container Registry
docker build -t gcr.io/YOUR_PROJECT/harbourmind:latest .
docker push gcr.io/YOUR_PROJECT/harbourmind:latest
```

### Run Locally

```bash
# Run the Docker container
docker run -p 8000:8080 \
  -e GEMINI_API_KEY=your-key \
  -e LLAMAPARSE_API_KEY=your-key \
  -e GCP_PROJECT_ID=your-project \
  harbourmind:latest

# Visit http://localhost:8000
```

---

## ☁️ Google Cloud Integration

### Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                    Google Cloud Run                           │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  HarbourMind Container                                 │  │
│  │  • FastAPI Application (uvicorn on port 8080)         │  │
│  │  • Serves website (index.html)                        │  │
│  │  • Handles PDF uploads                                │  │
│  │  • Calculates tariffs                                 │  │
│  │  • Returns JSON results                               │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
         ↓                                              ↓
   ┌──────────────┐                        ┌──────────────────────┐
   │ Cloud Logs   │                        │  Cloud Storage (GCS) │
   │              │                        │                      │
   │ • Metrics    │                        │ • Calculation logs   │
   │ • Traces     │                        │ • PDF files          │
   │ • Errors     │                        │ • Bucket: logs-XXXX  │
   └──────────────┘                        └──────────────────────┘
         ↓                                              ↓
   ┌──────────────────┐                   ┌──────────────────────┐
   │  Cloud Logging   │                   │  Cloud Console       │
   │  Dashboard       │                   │  gsutil commands     │
   └──────────────────┘                   └──────────────────────┘
```

### Configuration

#### Environment Variables (set in Cloud Run)

```bash
# Go to Cloud Console → Cloud Run → harbourmind → Edit & Deploy

GEMINI_API_KEY=AIzaSyA...your-key
LLAMAPARSE_API_KEY=llx-...your-key
GCP_PROJECT_ID=your-gcp-project-id
GCS_BUCKET_NAME=harbourmind-logs-your-project
CORS_ORIGINS=*
APP_ENV=production
LOG_LEVEL=INFO
```

#### Cloud Run Service Configuration

| Setting | Value | Purpose |
|---------|-------|---------|
| **Memory** | 2 GB | PDF processing requires memory |
| **Timeout** | 540 sec (9 min) | Large PDFs need processing time |
| **Max Instances** | 100 | Auto-scaling limit |
| **Min Instances** | 1 | Keep service warm (always available) |
| **CPU** | Allocated during requests | Scales automatically |
| **Port** | 8080 | Container port (Dockerfile line 31) |

### Cloud Storage Integration

Your app uses `CloudStorageManager` class (src/utils/cloud_storage.py):

#### Development Mode (use_mock=True)
```python
from src.utils.cloud_storage import CloudStorageManager

# Local development - in-memory storage
storage = CloudStorageManager(
    project_id="0458830062",
    bucket_name="harbourmind-logs-0458830062",
    use_mock=True  # ← No real GCS needed
)

# Files stored in memory during session
logs = storage.list_logs(port="durban", limit=10)
```

#### Production Mode (use_mock=False)
```python
# Cloud Run - real Google Cloud Storage
storage = CloudStorageManager(
    project_id="your-project",
    bucket_name="harbourmind-logs-your-project",
    use_mock=False  # ← Uses google-cloud-storage library
)

# Files stored in GCS bucket
# Path: gs://harbourmind-logs-xxx/logs/2026-05-07/calc_abc123.json
```

### Log Storage Structure

```
gs://harbourmind-logs-0458830062/
├── logs/
│   ├── 2026-05-07/
│   │   ├── calc_abc123.json
│   │   ├── calc_def456.json
│   │   └── calc_ghi789.json
│   ├── 2026-05-06/
│   │   ├── calc_xyz789.json
│   │   └── ...
│   └── 2026-05-05/
│       └── ...
├── pdfs/
│   ├── 2026-05-07/
│   │   ├── tariff.pdf
│   │   └── vessel.pdf
│   └── ...
```

Each log file contains:
```json
{
  "calculation_id": "calc_abc123",
  "timestamp": "2026-05-07T10:30:00Z",
  "vessel_name": "SUDESTADA",
  "port": "durban",
  "charges": [
    {
      "charge_type": "light_dues",
      "amount": 12345.67,
      "confidence": 0.95
    },
    ...
  ],
  "subtotal": 50000.00,
  "vat_amount": 7500.00,
  "grand_total": 57500.00,
  "processing_time_ms": 1234,
  "status": "success"
}
```

---

## 🚀 Deployment Flow

### Step 1: Build Docker Image

```bash
# From project root
docker build -t gcr.io/YOUR_PROJECT/harbourmind:latest .
```

**What happens:**
1. Downloads python:3.11-slim base image
2. Installs requirements.txt packages
3. Copies application code
4. Sets up uvicorn server

### Step 2: Push to Container Registry

```bash
docker push gcr.io/YOUR_PROJECT/harbourmind:latest
```

**What happens:**
1. Uploads image to Google Container Registry (gcr.io)
2. Image available for Cloud Run to deploy
3. Previous versions kept for rollback

### Step 3: Deploy to Cloud Run

#### Option A: gcloud CLI
```bash
gcloud run deploy harbourmind \
  --image gcr.io/YOUR_PROJECT/harbourmind:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --timeout 540 \
  --max-instances 100 \
  --set-env-vars \
    GEMINI_API_KEY=your-key,\
    LLAMAPARSE_API_KEY=your-key,\
    GCP_PROJECT_ID=your-project,\
    GCS_BUCKET_NAME=harbourmind-logs
```

#### Option B: Cloud Console
1. Go to https://console.cloud.google.com/run
2. Click "Create Service"
3. Select your image: `gcr.io/YOUR_PROJECT/harbourmind`
4. Set port to `8080`
5. Set memory to `2GB`
6. Set timeout to `540s`
7. Add environment variables
8. Deploy

**What happens:**
1. Cloud Run provisions container instances
2. Routes HTTPS traffic to your container
3. Auto-scales based on demand
4. Generates public URL: `https://harbourmind-xxx.us-central1.run.app`

### Step 4: Test Deployment

```bash
# Set your service URL
SERVICE_URL="https://harbourmind-xxx.us-central1.run.app"

# Health check
curl ${SERVICE_URL}/health

# API test
curl ${SERVICE_URL}/api/v1/logs

# Visit in browser
open ${SERVICE_URL}
```

---

## 📊 Monitoring & Logs

### View Cloud Run Logs

```bash
# Stream logs (follow mode)
gcloud run logs read harbourmind \
  --platform managed \
  --region us-central1 \
  --follow

# Get last 50 log lines
gcloud run logs read harbourmind \
  --platform managed \
  --region us-central1 \
  --limit 50
```

### View Cloud Storage

```bash
# List all logs
gsutil ls -r gs://harbourmind-logs-your-project/

# List logs for specific date
gsutil ls gs://harbourmind-logs-your-project/logs/2026-05-07/

# Download a log file
gsutil cp gs://harbourmind-logs-your-project/logs/2026-05-07/calc_abc123.json .

# Count files
gsutil ls -r gs://harbourmind-logs-your-project/ | wc -l
```

### Cloud Console Dashboard

1. Go to https://console.cloud.google.com/run/detail/us-central1/harbourmind/metrics
2. View:
   - Request count
   - Error rate
   - Memory usage
   - CPU usage
   - Latency

---

## 💾 Requirements.txt Dependencies

For Docker, these packages are installed:

```
# Web Framework
fastapi>=0.111.0           # API framework
uvicorn[standard]>=0.29.0  # ASGI server

# LLM & AI
langchain>=0.2.0                    # LLM orchestration
langchain-google-genai>=1.0.0       # Google Gemini integration
google-generativeai>=0.5.0          # Google AI API

# Document Parsing (for PDF extraction)
llama-parse>=0.4.0                  # LlamaParse document parsing
llama-index-core>=0.10.0            # LlamaIndex framework

# Data Validation
pydantic>=2.7.0                     # Data validation
pydantic-settings>=2.2.0            # Settings management

# Calculation
numexpr>=2.10.0                     # Fast numerical expressions
numpy>=1.26.0                       # Numerical computing

# Utilities
python-dotenv>=1.0.0                # Load .env files
httpx>=0.27.0                       # Async HTTP client
python-multipart>=0.0.9             # File upload handling

# Testing
pytest>=8.2.0                       # Testing framework
pytest-asyncio>=0.23.0              # Async test support
```

**For Production Cloud Run:**
- Add `google-cloud-storage>=2.10.0` to use real GCS
- Add `google-cloud-logging>=3.5.0` for Cloud Logging integration

---

## 🔒 Security Best Practices

### 1. Don't Commit Sensitive Data
```bash
# ❌ BAD: secrets in code
API_KEY = "AIzaSyA..."

# ✅ GOOD: environment variables
API_KEY = os.environ.get("GEMINI_API_KEY")
```

### 2. Use Cloud Secrets Manager (Advanced)
```bash
# Create secret
gcloud secrets create gemini-api-key --data-file=-

# Reference in Cloud Run
gcloud run deploy harbourmind \
  --set-env-vars GEMINI_API_KEY=/secrets/gemini-api-key
```

### 3. Disable Public Access (Production)
```bash
gcloud run update harbourmind \
  --no-allow-unauthenticated
```

Then use IAM to grant access:
```bash
gcloud run add-iam-policy-binding harbourmind \
  --member=serviceAccount:your-sa@project.iam.gserviceaccount.com \
  --role=roles/run.invoker
```

### 4. Use VPC Connector for Private Database
```bash
gcloud run update harbourmind \
  --vpc-connector=harbourmind-connector
```

---

## 📈 Cost Optimization

### 1. Reduce Min Instances to 0
```bash
gcloud run update harbourmind \
  --min-instances 0
```
- Saves ~$30/month (no idle instances)
- First request has 1-2 second cold start

### 2. Set Lifecycle Policy for Old Logs
```bash
# Create lifecycle.json
cat > lifecycle.json <<EOF
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {"age": 90}
      }
    ]
  }
}
EOF

# Apply to bucket
gsutil lifecycle set lifecycle.json gs://harbourmind-logs-your-project
```

### 3. Monitor Billing
```bash
gcloud billing budgets list
gcloud billing budgets describe BUDGET_ID
```

---

## 🔄 Rollback to Previous Version

```bash
# List all revisions
gcloud run revisions list \
  --service harbourmind \
  --region us-central1

# Traffic currently on revision
gcloud run services describe harbourmind \
  --region us-central1 \
  --format='value(status.traffic[])'

# Rollback to previous revision
gcloud run update harbourmind \
  --region us-central1 \
  --revision=harbourmind-00015
```

---

## ✅ Checklist

- [ ] Docker builds without errors
- [ ] Container runs locally with `docker run`
- [ ] Pushed to Container Registry (gcr.io)
- [ ] Cloud Run service deployed
- [ ] Environment variables set in Cloud Run
- [ ] Health check `/health` returns 200
- [ ] Website loads at service URL
- [ ] PDF upload works
- [ ] Logs appear in Cloud Storage
- [ ] GCS bucket created and accessible
- [ ] Cloud Logging shows application logs

---

## 📚 Resources

- [Google Cloud Run Docs](https://cloud.google.com/run/docs)
- [Docker Best Practices](https://docs.docker.com/develop/dev-best-practices/)
- [Google Cloud Storage Docs](https://cloud.google.com/storage/docs)
- [Cloud Run Pricing](https://cloud.google.com/run/pricing)
- [Dockerfile Reference](https://docs.docker.com/engine/reference/builder/)
