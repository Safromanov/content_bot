# Content Bot

Personal Telegram bot that collects content from social networks, enriches it
with AI, and stores structured records and attachments in NocoDB.

## Supported content

- YouTube, Instagram, Threads, TikTok, VK, LinkedIn, Telegram, and regular web links
- Telegram text, photos, albums, video, voice, and audio
- Instagram carousels with all images preserved; video items are skipped
- Speech transcription with Groq Whisper
- Multi-image analysis with Gemini
- Category classification, titles, summaries, and translation

## Project structure

```text
content_bot/
  ai/          AI clients, classification, and text/image analysis
  media/       platform extraction, metadata, downloads, transcription
  storage/     NocoDB, retry queue, and statistics
  bot.py       Telegram application setup
  handlers.py  commands, albums, and background queue
  processor.py processing orchestration
tests/         unit tests
docs/          architecture documentation
bot.py         backward-compatible entry point
```

## Setup

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt
cp docs/.env.example .env
```

Fill in the required values in `.env`, then start the bot:

```bash
./venv/bin/python bot.py
```

The package entry point is also available:

```bash
./venv/bin/python -m content_bot
```

## systemd deployment

```bash
mkdir -p ~/.config/systemd/user
cp deploy/content-bot.service ~/.config/systemd/user/content-bot.service
systemctl --user daemon-reload
systemctl --user enable --now content-bot.service
```

To start the user service automatically at boot without an interactive login,
enable lingering once:

```bash
sudo loginctl enable-linger bot
```

Service management:

```bash
systemctl --user status content-bot.service
systemctl --user restart content-bot.service
journalctl --user -u content-bot.service -f
```

## Commands

- `/start` - usage overview
- `/status` - queue and failed-task status
- `/stats` - processing statistics
- `/dlq` - recent failed tasks
- `Change category` button - update a saved NocoDB record

## Tests

```bash
./venv/bin/python -m unittest discover -s tests -v
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the processing pipeline
and integration details.
