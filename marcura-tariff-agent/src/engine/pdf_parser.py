"""
PDF parser using LlamaParse API for document text extraction.

Includes an on-disk content-hash cache so repeated requests for the same
PDF skip the LlamaParse round-trip entirely.
"""

import hashlib
import logging
import asyncio
from pathlib import Path
from typing import Optional
import aiohttp
from src.utils.config import Config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


_PARSE_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / ".parse_cache"


def _cache_path(pdf_bytes: bytes) -> Path:
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    return _PARSE_CACHE_DIR / f"{digest}.md"


def _read_cached(pdf_bytes: bytes) -> Optional[str]:
    p = _cache_path(pdf_bytes)
    if p.exists():
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return None
    return None


def _write_cached(pdf_bytes: bytes, text: str) -> None:
    try:
        _PARSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(pdf_bytes).write_text(text, encoding="utf-8")
    except Exception as exc:
        logger.warning("PDF parse cache write failed: %s", exc)


async def extract_text_from_pdf(pdf_bytes: bytes, filename: str = "document.pdf") -> str:
    """Extract text from PDF using LlamaParse Cloud API, with disk cache."""
    cfg = Config()

    if not cfg.llamaparse_api_key:
        raise ValueError("LLAMAPARSE_API_KEY not configured")

    # ── Cache hit short-circuit ───────────────────────────────────────────
    cached = _read_cached(pdf_bytes)
    if cached is not None:
        print(
            f"[PDF_PARSER] CACHE HIT for {filename} "
            f"(sha256={hashlib.sha256(pdf_bytes).hexdigest()[:12]}, {len(cached)} chars)",
            flush=True,
        )
        return cached

    print(f"[PDF_PARSER] Starting extraction for {filename} ({len(pdf_bytes)} bytes)", flush=True)
    
    BASE_URL = "https://api.cloud.llamaindex.ai/api/parsing"
    headers = {"Authorization": f"Bearer {cfg.llamaparse_api_key}"}
    
    try:
        async with aiohttp.ClientSession() as session:
            # Step 1: Upload the file
            data = aiohttp.FormData()
            data.add_field('file', pdf_bytes, filename=filename, content_type='application/pdf')
            
            print(f"[PDF_PARSER] Uploading to {BASE_URL}/upload", flush=True)
            async with session.post(f"{BASE_URL}/upload", headers=headers, data=data) as resp:
                response_text = await resp.text()
                print(f"[PDF_PARSER] Upload response status: {resp.status}", flush=True)
                print(f"[PDF_PARSER] Upload response body: {response_text[:300]}", flush=True)
                
                if resp.status != 200:
                    raise ValueError(f"Upload failed: {resp.status} - {response_text}")
                
                result = await resp.json() if resp.status == 200 else {}
                
                # In second call, json was already consumed; re-parse from text
                import json
                result = json.loads(response_text)
                job_id = result.get('id')
                
                if not job_id:
                    raise ValueError(f"No job id in response: {result}")
                
                print(f"[PDF_PARSER] Job ID: {job_id}", flush=True)
            
            # Step 2: Poll for completion
            for attempt in range(60):  # Up to 5 minutes
                await asyncio.sleep(5)
                
                async with session.get(f"{BASE_URL}/job/{job_id}", headers=headers) as resp:
                    if resp.status != 200:
                        print(f"[PDF_PARSER] Status check {attempt}: HTTP {resp.status}", flush=True)
                        continue
                    
                    job = await resp.json()
                    status = job.get('status', 'UNKNOWN')
                    print(f"[PDF_PARSER] Status check {attempt}: {status}", flush=True)
                    
                    if status == 'SUCCESS':
                        # Step 3: Get the result
                        async with session.get(f"{BASE_URL}/job/{job_id}/result/markdown", headers=headers) as result_resp:
                            if result_resp.status != 200:
                                error_text = await result_resp.text()
                                raise ValueError(f"Failed to get result: {result_resp.status} - {error_text}")
                            
                            result_data = await result_resp.json()
                            text = result_data.get('markdown', '')
                            
                            if not text:
                                raise ValueError(f"Empty result: {result_data}")

                            print(f"[PDF_PARSER] Extracted {len(text)} characters", flush=True)
                            _write_cached(pdf_bytes, text)
                            return text
                    
                    if status in ('ERROR', 'FAILED', 'CANCELED'):
                        raise ValueError(f"Job failed with status: {status}. Details: {job}")
            
            raise ValueError("Job timed out after 5 minutes")
    
    except Exception as e:
        print(f"[PDF_PARSER] ERROR: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise ValueError(f"Failed to extract text from PDF: {str(e)}")
