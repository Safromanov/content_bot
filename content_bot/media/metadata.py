# metadata.py

import logging
import requests
from content_bot.config import cfg

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8',
}


def extract_youtube_metadata(url: str) -> dict:
    """oEmbed: title и author_name без скачивания."""
    try:
        resp = requests.get(
            'https://www.youtube.com/oembed',
            params={'url': url, 'format': 'json'}, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            'title':       data.get('title', ''),
            'author':      data.get('author_name', ''),
            'description': '',
            'url':         url,
        }
    except Exception as e:
        logger.warning('YouTube oEmbed: ' + str(e))
        return {'title': 'YouTube видео', 'author': '', 'description': '', 'url': url}


# Если og:description короче этого порога — считаем его сниппетом,
# лезем в тело страницы за реальным текстом статьи
_DESC_MIN_LEN = 200


def _extract_article_text(soup, max_chars: int = 1500) -> str:
    """
    Извлекает читаемый текст статьи из HTML.
    Ищет по убыванию специфичности: <article>, role=main, <main>,
    затем собирает <p> теги с достаточным количеством текста.
    Убирает навигацию, футеры, скрипты.
    """
    from bs4 import BeautifulSoup

    # Удаляем шум
    for tag in soup(['script', 'style', 'nav', 'footer', 'header',
                     'aside', 'form', 'button', 'noscript']):
        tag.decompose()

    # Ищем основной контейнер
    container = (
        soup.find('article') or
        soup.find(attrs={'role': 'main'}) or
        soup.find('main') or
        soup.find(class_=lambda c: c and any(
            k in c.lower() for k in ('article', 'content', 'post', 'entry', 'text')
        ))
    )
    root = container or soup

    # Собираем параграфы длиннее 60 символов
    paragraphs = []
    for p in root.find_all('p'):
        t = p.get_text(' ', strip=True)
        if len(t) > 60:
            paragraphs.append(t)

    text = ' '.join(paragraphs)
    return text[:max_chars].strip()


def extract_page_metadata(url: str) -> dict:
    """
    og:title + og:description + author + article text.

    Если og:description короче _DESC_MIN_LEN символов (сниппет, а не описание),
    вытаскиваем текст из тела статьи — это даёт классификатору достаточно контекста.
    Хабр, Medium, личные блоги — все отдают читаемый текст в <article> или <p>.
    """
    result = {'title': '', 'author': '', 'description': '', 'url': url}
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        def og(prop):
            tag = soup.find('meta', property='og:' + prop)
            return tag['content'].strip() if tag and tag.get('content') else ''

        def meta_name(name):
            tag = soup.find('meta', attrs={'name': name})
            return tag['content'].strip() if tag and tag.get('content') else ''

        result['title']  = og('title') or (soup.title.string.strip() if soup.title else '')
        result['author'] = (og('article:author') or meta_name('author')
                            or meta_name('article:author') or '')

        og_desc = og('description') or meta_name('description') or ''

        if len(og_desc) >= _DESC_MIN_LEN:
            # og:description достаточно подробное — используем его
            result['description'] = og_desc
            desc_src = 'og'
        else:
            # Сниппет слишком короткий — берём текст из тела статьи
            article_text = _extract_article_text(soup)
            if article_text:
                # Префикс og:description если есть — даёт дополнительный контекст
                result['description'] = (og_desc + ' ' + article_text).strip() if og_desc else article_text
                desc_src = 'article'
            else:
                result['description'] = og_desc
                desc_src = 'og_short'

        logger.info('Метаданные [' + desc_src + ']: title=' + result['title'][:60] +
                    ' desc_len=' + str(len(result['description'])) +
                    ' author=' + result['author'][:30])
    except Exception as e:
        logger.warning('extract_page_metadata: ' + str(e)[:100])
    return result



def extract_metadata(url: str) -> dict:
    """Универсальный метод — роутит по типу URL."""
    if 'youtube.com' in url or 'youtu.be' in url:
        return extract_youtube_metadata(url)
    return extract_page_metadata(url)
