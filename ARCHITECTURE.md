# TG-Bot Architecture

Telegram-бот для сбора контента из соцсетей → обогащение через LLM → запись в NocoDB.

## Data Flow

```
User message → bot.py → handlers.py (detect + queue) → processor.py (orchestrator)
                                                            ↓
                                                     media_handlers.py (enrich by platform)
                                                            ↓
                                              ┌─────────────┼──────────────┐
                                         transcription.py  text_utils.py  classifier.py
                                         (Whisper, yt-dlp)  (LLM: vision,  (LLM: category
                                                            summary, title) classification)
                                                            ↓
                                                     nocodb_writer.py → NocoDB API
```

## Modules

| File | Lines | Description |
|------|-------|-------------|
| `bot.py` | 57 | Entry point, telegram polling, command registration |
| `config.py` | 82 | Config from `.env`, categories list, validation |
| `detector.py` | 119 | `detect(message)` → ContentType enum + URL/file_id extraction |
| `handlers.py` | 249 | Message handler, album buffer, asyncio.Queue, retry worker, DLQ |
| `processor.py` | 209 | Orchestrator: handler.enrich → classify + title → write_record |
| `media_handlers.py` | 350 | Strategy pattern: YouTubeHandler, SocialHandler, TelegramVideoHandler, PhotoHandler, TextHandler |
| `transcription.py` | 483 | Whisper via Groq, YouTube subtitles, Instagram 3-source metadata, yt-dlp audio |
| `text_utils.py` | 133 | LLM calls: analyze_photo (vision), generate_theme_title, generate_summary, translate_to_russian |
| `classifier.py` | 117 | LLM classification into 16 categories with confidence |
| `nocodb_writer.py` | 269 | Build text field, upload files, POST record to NocoDB |
| `metadata.py` | 133 | YouTube/page oEmbed, article text extraction via BeautifulSoup |
| `groq_client.py` | 16 | Singleton Groq client |
| `stats.py` | 104 | Stats tracking (by category/platform), /stats command |
| `retry_utils.py` | 97 | Retry decorator, DLQ save/count |
| `miro_writer.py` | 292 | (Optional) Miro board integration, currently not wired into main pipeline |

## LLM Calls (Groq API)

| Call | Model | Where | Tokens ~est |
|------|-------|-------|-------------|
| Whisper transcription | whisper-large-v3 | `transcription.py:transcribe_file` | audio-based |
| Format transcript | llama-3.1-8b | `transcription.py:format_transcript` | 1500/chunk |
| Classification | llama-3.3-70b | `classifier.py:classify` | 500-800 |
| Theme title | llama-3.3-70b | `text_utils.py:generate_theme_title` | 100-200 |
| Summary | llama-3.1-8b | `text_utils.py:generate_summary` | 300-500 |
| Vision (photo) | llama-4-scout | `text_utils.py:analyze_photo` | 200-400 |
| Video description | llama-3.1-8b | `text_utils.py:generate_video_description` | 200-300 |
| Translation | llama-3.1-8b | `text_utils.py:translate_to_russian` | 500-1000 |

## ContentType Routing (media_handlers.py)

| ContentType | Handler | enrich() logic |
|-------------|---------|----------------|
| YOUTUBE | YouTubeHandler | oEmbed + subtitles API (parallel) |
| INSTAGRAM | SocialHandler | 3-source metadata (instaloader/oEmbed/scrape) + media download + vision |
| TIKTOK, VK | SocialHandler | yt-dlp metadata + audio download + Whisper |
| VIDEO, AUDIO | TelegramVideoHandler | Download from Telegram → Whisper |
| PHOTO | PhotoHandler | Download photos → vision analysis if no caption |
| TEXT, OTHER_URL, LINKEDIN, TELEGRAM | TextHandler | Page metadata extraction |

## NocoDB Record Schema

Fields: `Theme`, `Date`, `Text`, `URL`, `Platform`, `Category`, `Author`, `Attachment`

## Key Design Notes

- **Album handling**: Photos with `media_group_id` are buffered 2s before processing as one task
- **Instagram 3-source**: instaloader (full caption + CDN URLs) > oEmbed (truncated caption) > HTTP scrape (og:tags)
- **Translation guard**: `_is_mostly_russian()` skips LLM translation call for Russian text (>60% Cyrillic)
- **Markdown escaping**: `_escape_markdown_headings()` prevents NocoDB from rendering `#hashtags` as headings
- **Summary threshold**: `generate_summary` skipped for transcripts <300 chars
- **Retry**: 3 attempts with exponential backoff (1s, 2s, 4s), then DLQ
