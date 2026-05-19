# Polymarket Signal Bot

Scans **every active Polymarket market** and sends Telegram alerts for markets with unrealistically low or near-certain probabilities (e.g. "Will X become Iran's leader?" at 1%).

## Setup

### 1. Create your Telegram bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** it gives you

### 2. Get your Telegram Chat ID

1. Message **@userinfobot** on Telegram
2. It replies with your user ID — that's your Chat ID

### 3. Configure the bot

```bash
cp .env.example .env
```

Edit `.env`:

```
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=987654321
LOW_THRESHOLD=5
HIGH_THRESHOLD=95
SCAN_INTERVAL_MINUTES=60
MIN_LIQUIDITY=100
```

- **LOW_THRESHOLD** — alert if YES probability is **below** this % (catches "long shots")
- **HIGH_THRESHOLD** — alert if YES probability is **above** this % (catches near-certainties)
- **MIN_LIQUIDITY** — skip markets with less than this USD in liquidity (filters ghost markets)

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Run

```bash
python main.py
```

The bot will:
1. Send a startup message to your Telegram
2. Immediately run a full scan of all Polymarket markets
3. Send one batched message per 10 signals found
4. Repeat every `SCAN_INTERVAL_MINUTES` minutes

## Example alerts

```
🚨 POLYMARKET SIGNAL ALERT 🚨

Scanned 4,821 markets
Found 38 extreme-probability signals

LONG SHOT 🎰
Will Clav become Iran's Supreme Leader by 2025?
Probability: 1.2% | Liquidity: $3,400
🔗 View Market

LONG SHOT 🎰
Will Bitcoin reach $1M by June 2025?
Probability: 2.8% | Liquidity: $180,000
🔗 View Market
```

## Running as a service (Linux)

```bash
sudo nano /etc/systemd/system/polymarket-bot.service
```

```ini
[Unit]
Description=Polymarket Signal Bot
After=network.target

[Service]
WorkingDirectory=/path/to/Ultimate-signals
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now polymarket-bot
```
