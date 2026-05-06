import subprocess
import time
import requests
import json
import sys

BASE_URL = "http://localhost:8000"

print()
print("=" * 70)
print("PRIORITY 6: FastAPI ENDPOINTS TEST")
print("=" * 70)
print()

# Start the server in background
print("[1] Starting FastAPI server on port 8000...")
server_process = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
    cwd=r"Z:\HarbourMind\marcura-tariff-agent",
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

# Wait for server to start
time.sleep(3)

try:
    # Test 1: Health check
    print("[2] Testing GET /health...")
    response = requests.get(f"{BASE_URL}/health", timeout=5)
    assert response.status_code == 200, f"Health check failed: {response.text}"
    health = response.json()
    assert health["status"] == "healthy", "Status not healthy"
    print(f"  [OK] Health check passed")
    print(f"  [OK] Status: {health['status']}")
    print()

    # Test 2: Root endpoint
    print("[3] Testing GET /...")
    response = requests.get(f"{BASE_URL}/", timeout=5)
    assert response.status_code == 200, f"Root endpoint failed: {response.text}"
    root = response.json()
    assert "service" in root, "Missing service field"
    print(f"  [OK] Root endpoint working")
    print(f"  [OK] Service: {root['service']}")
    print()

    # Test 3: Calculation endpoint
    print("[4] Testing POST /api/v1/calculate...")
    payload = {
        "vessel_data": {
            "type": "Bulk Carrier",
            "gross_tonnage": 51300,
            "name": "SUDESTADA"
        },
        "port": "durban",
        "target_dues": ["Light Dues", "Port Dues", "Towage", "VTS Dues", "Pilotage", "Running Lines"]
    }

    response = requests.post(f"{BASE_URL}/api/v1/calculate", json=payload, timeout=10)
    print(f"  Status Code: {response.status_code}")
    assert response.status_code == 200, f"Calculation failed: {response.text}"
    result = response.json()

    # Validate response structure
    assert "charges" in result, "Missing charges in response"
    assert "subtotal" in result, "Missing subtotal"
    assert "grand_total" in result, "Missing grand_total"
    print(f"  [OK] Calculation endpoint working")
    print(f"  [OK] Charges calculated: {len(result['charges'])}")
    print()

    # Display charges
    print("  Charges breakdown:")
    for charge in result["charges"]:
        print(f"    {charge['charge_type']:20s}: {charge['amount']:12.2f} ZAR")

    print(f"  {'-' * 50}")
    print(f"  Subtotal:          {result['subtotal']:12.2f} ZAR")
    print(f"  VAT (15%):         {result['vat_amount']:12.2f} ZAR")
    print(f"  Grand Total:       {result['grand_total']:12.2f} ZAR")
    print()

    # Verify ground truth match
    expected_total = 215713.80
    assert abs(result["grand_total"] - expected_total) < 0.01,         f"Ground truth mismatch: expected {expected_total}, got {result['grand_total']}"
    print(f"  [OK] GROUND TRUTH MATCH: {result['grand_total']} ZAR")
    print()

    # Test 4: Trace log
    print("[5] Trace log verification...")
    assert "calculation_trace" in result, "Missing calculation trace"
    trace = result["calculation_trace"]
    print(f"  [OK] Trace log present ({len(trace)} steps)")
    for step in trace:
        print(f"    - {step}")
    print()

    print("=" * 70)
    print("[SUCCESS] PRIORITY 6 COMPLETE - API ENDPOINTS WORKING")
    print("=" * 70)
    print()
    print("[OK] Health check endpoint: GET /health")
    print("[OK] Root endpoint: GET /")
    print("[OK] Calculation endpoint: POST /api/v1/calculate")
    print(f"[OK] Ground truth match: {result['grand_total']} ZAR")
    print()
    print("API is ready for integration.")
    print()

except requests.exceptions.ConnectionError as e:
    print(f"ERROR: Could not connect to API server: {e}")
    print("Make sure the server is running on port 8000")
    sys.exit(1)
except AssertionError as e:
    print(f"ERROR: {e}")
    sys.exit(1)
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    # Terminate server
    server_process.terminate()
    try:
        server_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server_process.kill()
    print("[INFO] Server stopped.")
