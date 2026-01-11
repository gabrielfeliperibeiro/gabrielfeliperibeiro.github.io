# Djokobilas - Tennis Court Booking Automation for Hong Kong

Automate the booking of tennis courts through the LCSD (Leisure and Cultural Services Department) online booking system in Hong Kong.

## Features

- Automated login to LCSD booking system
- Support for multiple tennis venues across Hong Kong
- Configurable preferred time slots and days
- Scheduled booking at optimal times (midnight when slots open)
- Retry logic with exponential backoff
- Telegram notifications for booking status
- Headless browser mode for server deployment
- Human-like behavior to avoid detection

## Supported Venues

### Hong Kong Island
- Victoria Park Tennis Centre (Causeway Bay) - 14 courts
- Hong Kong Park Tennis Centre (Central) - 6 courts
- Wong Nai Chung Gap Road Tennis Courts (Happy Valley) - 6 courts
- Bowen Road Tennis Courts (Mid-Levels) - 4 courts

### Kowloon
- Kowloon Tsai Park Tennis Courts (Kowloon City) - 6 courts
- Morse Park Tennis Courts (Wong Tai Sin) - 8 courts
- King George V Memorial Park Tennis Courts (Jordan) - 4 courts

### New Territories
- Sha Tin Sports Ground Tennis Courts - 8 courts
- Tuen Mun Tang Shiu Kin Sports Ground - 6 courts
- Tin Shui Wai Sports Ground - 4 courts

## Installation

### Prerequisites

- Python 3.8 or higher
- Google Chrome browser
- LCSD account (register at https://www.lcsd.gov.hk/)

### Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/djokobilas.git
cd djokobilas
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Run the setup wizard:
```bash
python tennis_booking.py --setup
```

## Configuration

Copy `config.example.json` to `config.json` and modify:

```json
{
    "username": "your_lcsd_username",
    "password": "your_lcsd_password",
    "preferred_venues": ["victoria_park", "hong_kong_park"],
    "preferred_times": ["18:00", "19:00", "20:00"],
    "preferred_days": ["Saturday", "Sunday"],
    "booking_advance_days": 7,
    "max_retries": 3,
    "retry_delay": 5,
    "headless": false,
    "telegram_bot_token": "",
    "telegram_chat_id": ""
}
```

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `username` | LCSD account username | Required |
| `password` | LCSD account password | Required |
| `preferred_venues` | List of venue keys | `["victoria_park"]` |
| `preferred_times` | Preferred time slots | `["18:00", "19:00"]` |
| `preferred_days` | Preferred days of the week | `["Saturday", "Sunday"]` |
| `booking_advance_days` | Days in advance to book | `7` |
| `max_retries` | Maximum retry attempts | `3` |
| `retry_delay` | Delay between retries (seconds) | `5` |
| `headless` | Run browser in headless mode | `false` |
| `telegram_bot_token` | Telegram bot token for notifications | `""` |
| `telegram_chat_id` | Telegram chat ID for notifications | `""` |

## Usage

### List Available Venues
```bash
python tennis_booking.py --venues
```

### Run Booking Immediately
```bash
python tennis_booking.py --book
```

### Schedule Booking (runs at midnight)
```bash
python tennis_booking.py --schedule
```

### Run in Headless Mode
```bash
python tennis_booking.py --book --headless
```

## Setting Up Telegram Notifications

1. Create a Telegram bot:
   - Message [@BotFather](https://t.me/botfather) on Telegram
   - Send `/newbot` and follow the instructions
   - Copy the bot token

2. Get your chat ID:
   - Start a chat with your bot
   - Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   - Find your chat ID in the response

3. Add to configuration:
```json
{
    "telegram_bot_token": "your_bot_token",
    "telegram_chat_id": "your_chat_id"
}
```

## Running on a Server

For automated daily booking, use cron (Linux) or Task Scheduler (Windows).

### Linux Cron Example
```bash
# Run at 11:55 PM daily
55 23 * * * cd /path/to/djokobilas && /path/to/venv/bin/python tennis_booking.py --schedule --headless
```

### Docker Deployment
```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
CMD ["python", "tennis_booking.py", "--schedule", "--headless"]
```

## Troubleshooting

### Common Issues

1. **ChromeDriver version mismatch**
   - The script uses `webdriver-manager` to automatically handle driver versions
   - If issues persist, manually download the correct ChromeDriver version

2. **Login failures**
   - Verify your LCSD credentials are correct
   - Check if LCSD has updated their login page

3. **No available slots**
   - Tennis courts in HK are highly competitive
   - Try running the scheduled booking to get slots exactly when they open

4. **Timeout errors**
   - Increase the WebDriverWait timeout in the script
   - Check your internet connection

## Legal Disclaimer

This tool is for personal use only. Please:
- Comply with LCSD terms of service
- Do not use for commercial purposes
- Do not abuse the booking system
- Be respectful of other users

## Contributing

Contributions are welcome! Please feel free to submit pull requests.

## License

MIT License - see LICENSE file for details.

## Acknowledgments

- Named after Novak Djokovic, one of the greatest tennis players
- Built for the tennis community in Hong Kong
