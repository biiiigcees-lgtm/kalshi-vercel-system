# Kalshi V2 Vercel System Workspace

Automated trading system for Kalshi prediction markets, deployed on Vercel with cron-driven execution. Includes a TradingView Pine Script indicator suite for manual chart analysis.

## Project Structure

```
kalshi_vercel_system/
├── vercel.json              # Vercel deployment config (crons + function tuning)
├── api/
│   └── index.py             # FastAPI serverless handler with WebSocket trading engine
├── kalshi_indicators.pine   # TradingView Pine Script v6 indicator suite
├── requirements.txt         # Python dependencies
└── README.md                # This file
```

## Deployment Instructions

### Prerequisites

- [Vercel CLI](https://vercel.com/docs/cli) installed (`npm i -g vercel`)
- A [Kalshi](https://kalshi.com) account with API credentials
- Kalshi RSA private key (PEM format)
- A unique `CRON_SECRET` for secure cron endpoint authorization

### Step 1: Set Environment Variables

Inject these via the Vercel Dashboard or CLI:

```bash
vercel env add KALSHI_API_KEY_ID        # Your Kalshi API Key ID
vercel env add KALSHI_PRIVATE_KEY_PEM   # RSA private key (replace \n with literal \\n)
vercel env add KALSHI_DEMO              # "true" for demo, "false" for live
vercel env add CRON_SECRET              # Secure random string for cron auth
```

### Step 2: Deploy

```bash
vercel --prod
```

### Step 3: Monitor

The cron endpoint fires automatically every 15 minutes (at :13, :28, :43, :58). Check Vercel Function Logs for execution results.

## Trading Strategy

The cron handler (`/api/cron`):
1. Connects to Kalshi WebSocket feed for the configured ticker
2. Collects 15 seconds of tick data
3. Computes VWAP slope via linear regression
4. Places a limit order if the slope exceeds ±0.05 threshold

## TradingView Indicator

Import `kalshi_indicators.pine` into TradingView for visual signals:
- **Squeeze Momentum** — Bollinger Bands vs Keltner Channel squeeze detection
- **UT Bot Alerts** — ATR-based trailing stop with buy/sell signals
- **WaveTrend Oscillator** — Overbought/oversold reversal detection