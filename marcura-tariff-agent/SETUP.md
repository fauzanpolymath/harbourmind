# HarbourMind Local Development Setup

## 🔒 Security-First Setup

### Important: Never Commit API Keys

All sensitive configuration is **gitignored** and will not be committed to the repository.

### Step 1: Create Your Local Environment File

```bash
# Copy the template to create your local environment
cp .hmenv.template .hmenv.txt
```

### Step 2: Get Your API Keys

#### Google Gemini API Key
1. Go to [Google AI Studio](https://aistudio.google.com/app/apikeys)
2. Click "Create API Key"
3. Copy the key and paste it in `.hmenv.txt` next to `GEMINI_API_KEY=`

#### LlamaParse API Key (for document parsing)
1. Go to [LlamaIndex Cloud](https://www.llamaindex.ai/)
2. Sign up and navigate to API keys
3. Copy the key and paste it in `.hmenv.txt` next to `LLAMAPARSE_API_KEY=`

#### GCP Project Setup (for Cloud Deployment)
1. Create a GCP project at [Google Cloud Console](https://console.cloud.google.com/)
2. Note your Project ID
3. Paste it in `.hmenv.txt` next to `GCP_PROJECT_ID=`

### Step 3: Edit .hmenv.txt

```bash
# Open and edit the file with your keys
nano .hmenv.txt   # or use your preferred editor
```

Your file should look like:
```env
GEMINI_API_KEY=AIzaSyA...your-actual-key-here...
GEMINI_MODEL=gemini-2.5-flash
LLAMAPARSE_API_KEY=llx-...your-actual-key-here...
GCP_PROJECT_ID=my-gcp-project
GCS_BUCKET_NAME=harbourmind-logs-my-gcp-project
CORS_ORIGINS=*
APP_ENV=development
LOG_LEVEL=INFO
DATA_DIR=./data
```

### Step 4: Verify Your Setup

```bash
# Python will automatically load .hmenv.txt
python -c "from src.utils.config import Config; c = Config(); print(c)"
```

You should see configuration printed without errors.

## 🚀 Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run the FastAPI server
python -m uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

# Visit in browser
open http://localhost:8000
```

## ☁️ Cloud Deployment

Your `.hmenv.txt` is **only for local development**. When deploying to Cloud Run:

```bash
# The deployment script uses environment variables
# Set them in Cloud Run console or via gcloud:

gcloud run deploy harbourmind \
  --set-env-vars GEMINI_API_KEY=your-key,LLAMAPARSE_API_KEY=your-key,GCP_PROJECT_ID=your-project \
  --image gcr.io/your-project/harbourmind:latest
```

See [DEPLOYMENT.md](./DEPLOYMENT.md) for full details.

## ⚠️ Security Checklist

- [ ] `.hmenv.txt` is in `.gitignore` (verified)
- [ ] Never commit `.hmenv.txt` to git
- [ ] Never share `.hmenv.txt` with anyone
- [ ] Rotate keys if `.hmenv.txt` is accidentally exposed
- [ ] Use strong API keys (Google generates secure ones)
- [ ] In production, use Cloud Run secrets or IAM roles instead

## 🔄 What to Do if Keys Are Accidentally Exposed

1. **Delete the exposed keys immediately** from your API provider
2. **Generate new keys** in your API provider console
3. **Update `.hmenv.txt`** with new keys
4. **Never commit** the old keys (they've been removed from our history)

## 📚 File Structure

```
.
├── .hmenv.template      ← Template (safe to commit)
├── .hmenv.txt           ← YOUR KEYS (gitignored, never commit)
├── .gitignore           ← Lists .hmenv.txt as ignored
├── src/
│   ├── api/
│   │   └── main.py      ← FastAPI application
│   └── utils/
│       ├── config.py    ← Loads environment variables
│       ├── logger.py    ← Structured logging
│       └── cloud_storage.py  ← GCS integration
├── requirements.txt
├── SETUP.md             ← This file
├── DEPLOYMENT.md        ← Cloud Run deployment guide
└── Dockerfile
```

## 🆘 Troubleshooting

### "GEMINI_API_KEY is not set"
- Ensure `.hmenv.txt` exists and contains your key
- Check that the file is in the project root directory
- Verify the env variable name matches exactly (case-sensitive)

### "Module not found: google.cloud.storage"
- Run `pip install -r requirements.txt`
- Verify you're in a Python virtual environment

### "Connection refused" when accessing API
- Ensure the server is running: `python -m uvicorn src.api.main:app --reload`
- Check that port 8000 is not in use

### Can't upload PDFs
- Ensure `./data` directory exists: `mkdir -p data`
- Check that the app has write permissions to `./data`

## 📖 More Help

- [DEPLOYMENT.md](./DEPLOYMENT.md) - Full Cloud Run deployment guide
- [README.md](./README.md) - Project overview
- [Google Gemini API Docs](https://ai.google.dev/docs)
- [Cloud Run Docs](https://cloud.google.com/run/docs)
