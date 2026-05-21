import os
import time
import json
import base64
import datetime
import asyncio
import uuid
import requests
import websockets
from fastapi import FastAPI, Request, HTTPException
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding

app = FastAPI()

API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
DEMO_MODE = os.getenv("KALSHI_DEMO", "true").lower() == "true"
CRON_SECRET = os.getenv("CRON_SECRET")

BASE_URL = "https://external-api.demo.kalshi.co/trade-api/v2" if DEMO_MODE else "https://external-api.kalshi.com/trade-api/v2"
WS_URL = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2" if DEMO_MODE else "wss://external-api-ws.kalshi.com/trade-api/ws/v2"


def load_private_key():
    """Load the RSA private key from environment variable or local file."""
    pem_data = os.getenv("KALSHI_PRIVATE_KEY_PEM")
    if pem_data:
        pem_bytes = pem_data.replace("\\n", "\n").encode("utf-8")
    else:
        with open("kalshi_private_key.pem", "rb") as key_file:
            pem_bytes = key_file.read()

    return serialization.load_pem_private_key(
        pem_bytes,
        password=None,
        backend=default_backend()
    )


def sign_request(private_key, timestamp: str, method: str, path: str) -> str:
    """Create an RSA-PSS signature for Kalshi API authentication.

    Per Kalshi v2 spec: sign the canonical path WITHOUT query string.
    The message format is: timestamp + method + path.
    """
    # Strip query parameters from path for signing
    path_without_query = path.split("?")[0]
    message = f"{timestamp}{method}{path_without_query}".encode("utf-8")
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH
        ),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode("utf-8")


def get_auth_headers(method: str, path: str) -> dict:
    """Build authenticated headers for a Kalshi REST API call.

    The signing path must start with /trade-api/v2. If the provided path
    is relative, it's prefixed automatically.
    """
    signing_path = path if path.startswith("/trade-api/v2") else f"/trade-api/v2{path}"
    timestamp = str(int(datetime.datetime.now().timestamp() * 1000))
    private_key = load_private_key()
    signature = sign_request(private_key, timestamp, method, signing_path)
    return {
        "KALSHI-ACCESS-KEY": API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "Content-Type": "application/json"
    }


class ExecutionEngine:
    """Collects tick data via WebSocket, computes VWAP slope, and executes trades."""

    def __init__(self, ticker: str):
        self.ticker = ticker
        self.ticks: list[tuple[float, float, float]] = []  # (timestamp, price, volume)

    def calculate_vwap_slope(self, lookback_seconds: float = 30.0) -> float:
        """Compute the slope of VWAP over recent ticks using linear regression.

        Uses pure Python (no numpy) to minimize cold-start latency on Vercel.
        """
        now = time.time()
        self.ticks = [t for t in self.ticks if now - t[0] <= lookback_seconds]

        if len(self.ticks) < 5:
            return 0.0

        times = [t[0] for t in self.ticks]
        prices = [t[1] for t in self.ticks]
        volumes = [t[2] for t in self.ticks]

        start_time = times[0]
        norm_times = [t - start_time for t in times]

        cumulative_volume = 0.0
        cumulative_pv = 0.0
        vwaps: list[float] = []

        for t_norm, price, vol in zip(norm_times, prices, volumes):
            cumulative_volume += vol
            cumulative_pv += price * vol
            vwap = cumulative_pv / cumulative_volume if cumulative_volume > 0 else price
            vwaps.append(vwap)

        n = len(norm_times)
        sum_x = sum(norm_times)
        sum_y = sum(vwaps)
        sum_xx = sum(x ** 2 for x in norm_times)
        sum_xy = sum(x * y for x, y in zip(norm_times, vwaps))

        denominator = n * sum_xx - sum_x ** 2
        if abs(denominator) < 1e-8:
            return 0.0

        slope = (n * sum_xy - sum_x * sum_y) / denominator
        return slope

    def execute_order(self, side: str, price_cents: int, contracts: int) -> dict | None:
        """Place a limit order on Kalshi v2 via REST API.

        Per Kalshi v2 spec:
        - side="yes" → provide yes_price_dollars (price to buy YES for)
        - side="no"  → provide no_price_dollars  (price to buy NO for)
        - count is integer (whole contracts)
        """
        path = "/portfolio/orders"
        url = BASE_URL + path

        # Build payload matching Kalshi v2 /portfolio/orders spec
        payload: dict = {
            "ticker": self.ticker,
            "action": "buy",
            "type": "limit",
            "side": side,
            "count": contracts,
            "client_order_id": str(uuid.uuid4())
        }

        # Only set the price field for the relevant side
        # YES contracts: you pay yes_price (market's probability price)
        # NO contracts:  you pay no_price  (1 - yes_price)
        if side == "yes":
            payload["yes_price_dollars"] = f"{price_cents / 100:.4f}"
        else:
            payload["no_price_dollars"] = f"{price_cents / 100:.4f}"

        try:
            headers = get_auth_headers("POST", path)
            response = requests.post(url, headers=headers, json=payload)
            if response.status_code == 201:
                return response.json()
            else:
                print(f"Order failed: {response.status_code} | Details: {response.text}")
                return None
        except Exception as e:
            print(f"Exception during REST execution: {e}")
            return None


@app.get("/api/cron")
async def handle_trading_cycle(request: Request):
    """Main trading cycle triggered by Vercel Cron.

    Collects WebSocket tick data for 15 seconds, computes VWAP slope,
    and executes a directional trade if the signal exceeds thresholds.
    """
    if CRON_SECRET:
        auth_header = request.headers.get("Authorization")
        if not auth_header or auth_header != f"Bearer {CRON_SECRET}":
            raise HTTPException(status_code=401, detail="Unauthorized request source")

    if not API_KEY_ID:
        return {"status": "error", "message": "API credentials missing."}

    target_ticker = request.query_params.get("ticker", "KXBTC15M-26MAY110600-00")
    engine = ExecutionEngine(target_ticker)

    end_run_time = time.time() + 15.0

    try:
        private_key = load_private_key()
        timestamp = str(int(datetime.datetime.now().timestamp() * 1000))
        signature = sign_request(private_key, timestamp, "GET", "/trade-api/ws/v2")

        ws_headers = {
            "KALSHI-ACCESS-KEY": API_KEY_ID,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp
        }

        async with websockets.connect(WS_URL, extra_headers=ws_headers) as ws:
            subscription_msg = {
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["ticker"],
                    "market_ticker": target_ticker
                }
            }
            await ws.send(json.dumps(subscription_msg))

            while time.time() < end_run_time:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    data = json.loads(response)

                    if data.get("type") == "ticker":
                        msg = data.get("msg", {})
                        last_price_str = msg.get("last_price_dollars")

                        if last_price_str:
                            last_price = float(last_price_str)
                        else:
                            last_price = 0.50

                        volume = float(msg.get("volume") or msg.get("volume_24h") or 1.0)
                        engine.ticks.append((time.time(), last_price, volume))
                except asyncio.TimeoutError:
                    continue

    except Exception as e:
        return {"status": "websocket_error", "details": str(e)}

    slope = engine.calculate_vwap_slope()
    decision = "hold"
    execution_result = None

    # VWAP slope strategy: positive slope → buy YES, negative slope → buy NO
    # Buy YES at 20¢ (price_cents=20), Buy NO at 20¢ (price_cents=20)
    if slope > 0.05:
        decision = "yes"
        execution_result = engine.execute_order("yes", 20, 10)
    elif slope < -0.05:
        decision = "no"
        execution_result = engine.execute_order("no", 20, 10)

    return {
        "status": "success",
        "ticker": target_ticker,
        "slope": slope,
        "decision": decision,
        "execution": execution_result,
        "ticks_collected": len(engine.ticks)
    }


@app.get("/api/markets")
async def list_markets(limit: int = 10, status: str = "open"):
    """Public endpoint: list Kalshi markets with optional filters.

    No authentication required — calls the public /markets endpoint.
    Useful for health-checking API connectivity and finding tickers.
    """
    params = {"limit": min(limit, 100), "status": status}
    try:
        response = requests.get(f"{BASE_URL}/markets", params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            markets = data.get("markets", [])
            return {
                "status": "success",
                "count": len(markets),
                "markets": [
                    {
                        "ticker": m.get("ticker"),
                        "title": m.get("title"),
                        "status": m.get("status"),
                        "yes_bid": m.get("yes_bid"),
                        "yes_ask": m.get("yes_ask"),
                        "no_bid": m.get("no_bid"),
                        "no_ask": m.get("no_ask"),
                        "volume": m.get("volume"),
                    }
                    for m in markets
                ]
            }
        else:
            return {
                "status": "error",
                "http_status": response.status_code,
                "detail": response.text[:500]
            }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/api/health")
async def health_check():
    """Simple health-check endpoint (no auth required)."""
    return {
        "status": "ok",
        "demo_mode": DEMO_MODE,
        "api_configured": API_KEY_ID is not None,
        "base_url": BASE_URL,
        "ws_url": WS_URL
    }