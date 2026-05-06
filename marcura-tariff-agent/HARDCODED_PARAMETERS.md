# Hardcoded Parameters Review

## Summary

This document lists all hardcoded parameters found in the HarbourMind codebase. These are values that should ideally be configurable via environment variables or configuration files.

---

## 🔴 Critical Hardcoded Values

### 1. **src/api/main.py - API Configuration**

#### Line 16: API Title and Version
```python
app = FastAPI(title='HarbourMind Tariff Calculator', version='1.0.0')
```
**Issue:** Title and version are hardcoded
**Recommendation:** Move to `config.py` or environment variables
```python
# Should be:
title = os.environ.get("APP_TITLE", "HarbourMind Tariff Calculator")
version = os.environ.get("APP_VERSION", "1.0.0")
app = FastAPI(title=title, version=version)
```

---

#### Lines 19-25: CORS Settings
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*']
)
```
**Issue:** CORS allows everything (`*`)
**Recommendation:** Use config variable
```python
# Already partially done via .hmenv.txt:
cors_origins = cfg.cors_origins.split(',') if cfg.cors_origins else ['*']
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*']
)
```

---

#### Line 37: Default Confidence Score
```python
confidence: float = 0.90
```
**Issue:** Hardcoded default confidence value
**Recommendation:** Move to constants or config
```python
DEFAULT_CONFIDENCE = 0.90
# Or in config.py
confidence_default = os.environ.get("DEFAULT_CONFIDENCE", "0.90")
```

---

### 2. **src/api/main.py - Mock Tariff Rules**

#### Lines 62-69: Durban Port Rules (MAJOR HARDCODING)
```python
MOCK_DURBAN_RULES = RuleStore(port_name='durban', rules=[
    ExtractedRule(charge_type='light_dues', 
                  extraction_parameters={'base_fee': 0, 'rate': 0.95, 'unit': 1, 'multiplier': 1}, 
                  extraction_confidence=0.95, ...),
    ExtractedRule(charge_type='port_dues', 
                  extracted_parameters={'base_fee': 0, 'rate': 1.25, 'unit': 100, 'periods': 100}, 
                  extraction_confidence=0.90, ...),
    ExtractedRule(charge_type='towage', 
                  extracted_parameters={'brackets': [
                      {'min': 0, 'max': 10000, 'rate': 5000},
                      {'min': 10001, 'max': 50000, 'rate': 10000},
                      {'min': 50001, 'max': 100000, 'rate': 15000}
                  ], ...},
    ExtractedRule(charge_type='vts_dues', 
                  extracted_parameters={'fee': 12500, ...}),
    ExtractedRule(charge_type='pilotage', 
                  extracted_parameters={'fee': 18608.61, 'multiplier': 2, ...}),
    ExtractedRule(charge_type='running_lines', 
                  extracted_parameters={'fee': 10000, ...}),
])
```

**Issue:** Complete tariff structure hardcoded
**Affected Values:**
- Light dues: rate=0.95, unit=1
- Port dues: rate=1.25, unit=100, periods=100
- Towage brackets: 5000, 10000, 15000 rates
- VTS dues: fee=12500
- Pilotage: fee=18608.61, multiplier=2
- Running lines: fee=10000

**Recommendation:** Load from database or JSON config file
```python
# Could be JSON file: tariffs/durban.json
{
  "port_name": "durban",
  "rules": [
    {
      "charge_type": "light_dues",
      "calculation_logic": "Base fee plus incremental rate per unit",
      "extracted_parameters": {
        "base_fee": 0,
        "rate": 0.95,
        "unit": 1,
        "multiplier": 1
      },
      "extraction_confidence": 0.95,
      "required_variables": ["gross_tonnage"],
      "conditions": "Standard light dues"
    },
    // ... rest of rules
  ]
}

# Then load dynamically:
def load_tariff_for_port(port_name: str) -> RuleStore:
    with open(f"tariffs/{port_name}.json") as f:
        return RuleStore(**json.load(f))
```

---

### 3. **src/api/main.py - Mock Vessel Data**

#### Lines 137-152: Hardcoded Vessel Profile
```python
# Line 137-138
vessel_name = "SUDESTADA"
port = "durban"

# Lines 144-152
vessel_profile = VesselProfile(
    name=vessel_name,
    gross_tonnage=51300,
    length_overall=190.5,
    beam=32.2,
    draft=10.5,
    cargo_type="General Cargo",
    containers_teu=0
)
```

**Issue:** Mock vessel data is hardcoded
- Vessel name: "SUDESTADA"
- Port: "durban"
- Gross tonnage: 51300
- Length: 190.5
- Beam: 32.2
- Draft: 10.5
- Cargo type: "General Cargo"
- Containers: 0

**Recommendation:** Extract from PDF or accept as input parameters
```python
# This should come from PDF parsing:
@app.post('/api/v1/calculate-from-pdfs')
async def calculate_from_pdfs(
    tariff_pdf: UploadFile,
    vessel_pdf: UploadFile,
    vessel_name: Optional[str] = None,
    port: Optional[str] = None
):
    # Parse PDFs to extract actual data
    # Fall back to provided parameters if parsing fails
    actual_vessel_name = vessel_name or parse_vessel_name(vessel_pdf)
    actual_port = port or parse_port(tariff_pdf)
    actual_vessel_data = parse_vessel_specs(vessel_pdf) or {
        "gross_tonnage": 51300,
        # ... defaults
    }
```

---

### 4. **src/api/main.py - VAT Rate**

#### Lines 105, 158: VAT Rate Hardcoded
```python
# Line 105
vat_amount = subtotal * 0.15

# Line 158
vat_amount = subtotal * Decimal('0.15')
```

**Issue:** 15% VAT hardcoded
**Recommendation:** Move to config
```python
# In config.py
VAT_RATE = float(os.environ.get("VAT_RATE", "0.15"))

# In main.py
vat_amount = subtotal * VAT_RATE
```

---

### 5. **src/api/main.py - Confidence Scores**

#### Lines 170, 206: Extraction Confidence Hardcoded
```python
# Line 170
'confidence': 0.92

# Line 206
'extraction_confidence': 0.92,
```

**Issue:** Confidence score hardcoded to 0.92
**Recommendation:** Make configurable
```python
DEFAULT_EXTRACTION_CONFIDENCE = float(os.environ.get("DEFAULT_EXTRACTION_CONFIDENCE", "0.92"))

# Then use:
'confidence': DEFAULT_EXTRACTION_CONFIDENCE
```

---

### 6. **src/api/main.py - Currency**

#### Line 108: Currency Hardcoded
```python
currency='ZAR'
```

**Issue:** Currency hardcoded to ZAR (South African Rand)
**Recommendation:** Make configurable
```python
DEFAULT_CURRENCY = os.environ.get("DEFAULT_CURRENCY", "ZAR")

# Then use:
currency=DEFAULT_CURRENCY
```

---

### 7. **src/api/main.py - Website Path**

#### Lines 314-317: Website File Path
```python
website_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    'website',
    'index.html'
)
```

**Issue:** Hardcoded relative path to website
**Recommendation:** Make configurable
```python
website_path = os.path.join(
    cfg.website_dir,
    'index.html'
)

# In config.py:
website_dir = os.environ.get("WEBSITE_DIR", "src/website")
```

---

### 8. **src/api/main.py - API Endpoints**

#### Lines 327-333: Hardcoded API Endpoints
```python
'endpoints': {
    'health': 'GET /health',
    'calculate': 'POST /api/v1/calculate',
    'calculate_from_pdfs': 'POST /api/v1/calculate-from-pdfs',
    'logs': 'GET /api/v1/logs',
    'log_detail': 'GET /api/v1/logs/{calculation_id}',
    'docs': '/docs'
}
```

**Issue:** API endpoint paths hardcoded in fallback response
**Recommendation:** Use FastAPI introspection instead
```python
# Use FastAPI's route discovery
routes = {
    route.path: route.methods
    for route in app.routes
    if hasattr(route, 'methods')
}
```

---

### 9. **src/api/main.py - Trace Log Messages**

#### Line 77: Hardcoded Trace Messages
```python
trace_log = ['[1/5] Validating rule completeness...', 
             '[2/5] Validating vessel schema...', 
             '[3/5] Calculating charges...']
```

**Issue:** Step messages are hardcoded
**Recommendation:** Extract to constants or config
```python
CALCULATION_STEPS = [
    "[1/5] Validating rule completeness...",
    "[2/5] Validating vessel schema...",
    "[3/5] Calculating charges...",
]

trace_log = CALCULATION_STEPS.copy()
```

---

## 🟡 Minor Hardcoded Values

### Defaults in Function Parameters
These are acceptable defaults but could be configurable:

1. **Line 235: Default log limit**
   ```python
   limit: int = 50
   ```
   Could be configurable via environment variable

2. **Line 339: Uvicorn server port (when run directly)**
   ```python
   uvicorn.run(app, host='0.0.0.0', port=8000)
   ```
   Should match Dockerfile port (8080), currently mismatched!
   ⚠️ **BUG**: Main.py uses port 8000, but Dockerfile uses port 8080

---

## 📋 Configuration Priority

### High Priority (Should be Externalized)
1. ✅ Tariff rules (already partially in config via rules)
2. ✅ VAT rate
3. ✅ Currency
4. ✅ Vessel mock data
5. ✅ Default confidence scores
6. ✅ API title and version

### Medium Priority (Nice to Have)
1. ⚠️ CORS settings (partially done via cfg.cors_origins)
2. ⚠️ Default log limit
3. ⚠️ Calculation step messages

### Low Priority (Acceptable as-is)
1. ✅ Confidence default in Pydantic model
2. ✅ API endpoint documentation

---

## 🔧 Recommended Refactoring

### Create a Constants File
```python
# src/config/constants.py
DEFAULT_CONFIDENCE = 0.90
DEFAULT_EXTRACTION_CONFIDENCE = 0.92
DEFAULT_VAT_RATE = 0.15
DEFAULT_CURRENCY = "ZAR"
DEFAULT_LOG_LIMIT = 50

CALCULATION_STEPS = [
    "[1/5] Validating rule completeness...",
    "[2/5] Validating vessel schema...",
    "[3/5] Calculating charges...",
    "[4/5] Handling exceptions...",
    "[5/5] Complete"
]
```

### Update config.py
```python
# src/utils/config.py
class Config:
    vat_rate: float
    currency: str
    default_confidence: float
    default_extraction_confidence: float
    default_log_limit: int
    website_dir: str
    
    def __init__(self):
        self.vat_rate = float(os.environ.get("VAT_RATE", "0.15"))
        self.currency = os.environ.get("DEFAULT_CURRENCY", "ZAR")
        self.default_confidence = float(os.environ.get("DEFAULT_CONFIDENCE", "0.90"))
        self.default_extraction_confidence = float(os.environ.get("DEFAULT_EXTRACTION_CONFIDENCE", "0.92"))
        self.default_log_limit = int(os.environ.get("DEFAULT_LOG_LIMIT", "50"))
        self.website_dir = os.environ.get("WEBSITE_DIR", "src/website")
```

### Externalize Tariff Rules
```python
# tariffs/durban.json
{
  "port_name": "durban",
  "rules": [
    {
      "charge_type": "light_dues",
      "calculation_logic": "Base fee plus incremental rate per unit",
      "extracted_parameters": {
        "base_fee": 0,
        "rate": 0.95,
        "unit": 1,
        "multiplier": 1
      },
      "extraction_confidence": 0.95,
      "required_variables": ["gross_tonnage"],
      "conditions": "Standard light dues"
    }
    // ... more rules
  ]
}
```

### Fix Port Mismatch
```python
# In main.py
if __name__ == '__main__':
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))  # Match Dockerfile
    uvicorn.run(app, host='0.0.0.0', port=port)
```

---

## ✅ Summary Table

| Issue | Location | Severity | Type | Recommendation |
|-------|----------|----------|------|-----------------|
| API title/version | main.py:16 | Low | Hardcoded | Use env vars |
| CORS settings | main.py:19-25 | Medium | Hardcoded | Already in config, use it |
| Default confidence | main.py:37 | Low | Hardcoded | Use constant |
| **Tariff rules** | main.py:62-69 | **CRITICAL** | **Hardcoded** | **Load from JSON/DB** |
| **Vessel mock data** | main.py:137-152 | **CRITICAL** | **Hardcoded** | **Parse from PDFs** |
| VAT rate | main.py:105, 158 | High | Hardcoded | Use config |
| Confidence scores | main.py:170, 206 | Medium | Hardcoded | Use constants |
| Currency | main.py:108 | High | Hardcoded | Use config |
| Website path | main.py:314-317 | Low | Hardcoded | Use config |
| API endpoints | main.py:327-333 | Low | Hardcoded | Use introspection |
| Trace messages | main.py:77 | Low | Hardcoded | Use constants |
| **Port mismatch** | main.py:339 | **CRITICAL** | **Bug** | **Fix to 8080** |
| Log limit default | main.py:235 | Low | Hardcoded | Use config |

---

## Next Steps

1. **Immediate** (Before Production):
   - Fix port mismatch (main.py line 339: change 8000 → 8080)
   - Externalize tariff rules to JSON files
   - Move VAT rate to config

2. **Short Term** (Next Sprint):
   - Externalize all currency/confidence/message constants
   - Create proper tariff repository/database
   - Implement actual PDF parsing instead of mocks

3. **Long Term**:
   - Implement tariff rule versioning
   - Add multi-port support
   - Create admin interface for managing tariffs and rules
