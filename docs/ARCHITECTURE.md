# TG-Bot Architecture

Telegram-бот для сбора контента из соцсетей → обогащение через LLM → запись в NocoDB.

## Data Flow

```
User message → content_bot/bot.py → handlers.py (detect + queue) → processor.py
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
| `content_bot/bot.py` | Telegram polling and command registration |
| `content_bot/config.py` | Environment configuration and categories |
| `content_bot/detector.py` | Content type and URL/file extraction |
| `content_bot/handlers.py` | Commands, album buffer, queue worker, and DLQ |
| `content_bot/processor.py` | Enrichment, classification, title, and persistence orchestration |
| `content_bot/media/handlers.py` | Media strategy implementations by platform |
| `content_bot/media/transcription.py` | Whisper, subtitles, Instagram/VK extraction, and yt-dlp |
| `content_bot/media/metadata.py` | YouTube and webpage metadata extraction |
| `content_bot/ai/text_utils.py` | Gemini multi-image vision and Groq text tasks |
| `content_bot/ai/classifier.py` | Classification into configured categories |
| `content_bot/ai/groq_client.py` | Shared Groq client |
| `content_bot/storage/nocodb.py` | Attachments, records, and category updates |
| `content_bot/storage/stats.py` | Processing statistics |
| `content_bot/storage/retry.py` | Retry helpers and dead-letter queue |

## LLM Calls (Groq API)

| Call | Model | Where | Tokens ~est |
|------|-------|-------|-------------|
| Whisper transcription | whisper-large-v3 | `media/transcription.py:transcribe_file` | audio-based |
| Format transcript | llama-3.1-8b | `media/transcription.py:format_transcript` | 1500/chunk |
| Classification | llama-3.3-70b | `ai/classifier.py:classify` | 500-800 |
| Theme title | llama-3.3-70b | `ai/text_utils.py:generate_theme_title` | 100-200 |
| Summary | llama-3.1-8b | `ai/text_utils.py:generate_summary` | 300-500 |
| Vision (photo/carousel) | Gemini 3.5 Flash | `ai/text_utils.py:analyze_images` | image-based |
| Video description | llama-3.1-8b | `ai/text_utils.py:generate_video_description` | 200-300 |
| Translation | llama-3.1-8b | `ai/text_utils.py:translate_to_russian` | 500-1000 |

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
- **Deployment**: user systemd unit in `deploy/content-bot.service`; lingering enables boot startup
- **Shutdown**: the queue worker is cancelled and awaited during application shutdown
