# classifier.py

import json
import logging
import re
from dataclasses import dataclass
from config import cfg
from groq_client import get_groq

logger = logging.getLogger(__name__)

CATEGORY_DESCRIPTIONS = {
    'Photo':                'Photography, cameras, lenses, shooting, photo editing, Lightroom, RAW, composition',
    'Sport':                'Sports, workout, fitness, gym, running, swimming, cycling, exercises, muscle pain, stretching',
    'renovation':           'Home renovation, interior design, construction, flooring, furniture, apartment design',
    'AI':                   'Artificial intelligence, LLM, GPT, Claude, Gemini, Qwen, ML, benchmarks, model comparison, RAG, fine-tuning',
    'information security': 'SOC, SIEM, CVE, SSRF, XSS, RCE, OWASP, cybersecurity, hacking, vulnerabilities, exploits, agentic AI safety, AI agents attacks',
    'Health':               'Health, medicine, nutrition, sleep, psychology, vitamins, treatment, diet, wellness',
    'Cinema':               'Movies, films, series, TV shows, directors, actors, reviews, streaming, Netflix',
    'Cooking':              'Cooking, recipes, ingredients, kitchen techniques, food, baking, restaurants',
    'Travel':               'Travel, trips, countries, cities, hotels, flights, tourism, vacation, landmarks',
    'Games':                'Video games, board games, gaming, consoles, PC gaming, mobile games, Steam, PlayStation, Xbox',
    'Clothes':              'Clothing, fashion, style, outfits, brands, shoes, accessories, wardrobe, streetwear',
    'marketplace':          'Products to buy: gadgets, electronics, home goods, deals on Amazon, AliExpress, Ozon, Wildberries',
    'Design':               'Graphic design, UI/UX, typography, colors, fonts, branding, illustrations, layouts, Figma, Adobe',
    'Coding':               'Software development, programming, code, frameworks, libraries, databases, backend, frontend, DevOps',
    'Vacancy':              'Зарплата:, Требования:, Локация:, Job offers, job descriptions, hiring, career opportunities, work requirements, HR, salary, remote, junior/middle/senior, team, stack, recruitment',
    'other':                'Content that does not clearly fit any category above',
}


@dataclass
class ClassifyResult:
    category:   str
    confidence: float


def classify(text: str, task_id: str = "") -> ClassifyResult:
    """
    LLM-классификация. Модель явно анализирует хештеги из текста как
    сильные сигналы — они часто прямо указывают тему.
    """
    prefix = "[" + task_id + "] " if task_id else ""

    if not text.strip():
        return ClassifyResult(cfg.DEFAULT_CATEGORY, 0.0)

    categories_list = "\n".join(
        "- " + name + ": " + desc
        for name, desc in CATEGORY_DESCRIPTIONS.items()
    )
    valid_names = " | ".join(cfg.CATEGORIES)

    prompt = (
        "You are a content classifier. Your task:\n"
        "1. Extract all hashtags from the text (words starting with #)\n"
        "2. Identify the main topic from both the text body AND the hashtags\n"
        "3. Choose the single best matching category\n\n"

        "Valid categories: " + valid_names + "\n\n"
        "Category descriptions:\n" + categories_list + "\n\n"

        "Rules:\n"
        "- Hashtags are STRONG signals — they directly indicate the topic\n"
        "- Choose the MOST SPECIFIC matching category\n"
        "- \"Vacancy\" has PRIORITY: if the content is a job offer or hiring post, choose Vacancy even if it mentions Coding, InfoSec, or Design\n"
        "- \"other\" only if truly nothing fits after analyzing hashtags and text\n"
        "- \"information security\" covers ALL cybersecurity and hacking topics\n"
        "- \"marketplace\" is for purchasable products\n\n"

        "Respond with ONLY valid JSON, no explanation:\n"
        "{\"category\": \"<category_name>\", \"confidence\": <0.0-1.0>}\n\n"
        "Text to classify:\n" + text[:1500]
    )

    raw = ""
    try:
        response = get_groq().chat.completions.create(
            model=cfg.GROQ_MODEL_SMART,
            max_tokens=40,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = re.sub(r"```(?:json)?", "", response.choices[0].message.content.strip()).strip()
        data       = json.loads(raw)
        category   = str(data.get("category", "")).strip()
        confidence = float(data.get("confidence", 0.5))

        # Нормализация регистра
        if category not in cfg.CATEGORIES:
            for c in cfg.CATEGORIES:
                if c.lower() == category.lower():
                    category = c
                    break
            else:
                for c in cfg.CATEGORIES:
                    if re.search(rf'\b{re.escape(c.lower())}\b', category.lower()):
                        category = c
                        break
                else:
                    logger.warning(prefix + "Неизвестная категория: " + category)
                    category   = cfg.DEFAULT_CATEGORY
                    confidence = 0.0

        logger.info(prefix + "Classifier: " + category +
                    " (conf=" + str(round(confidence, 2)) + ")")
        return ClassifyResult(category, confidence)

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(prefix + "JSON error: " + str(e) + " raw=" + raw[:80])
        for c in cfg.CATEGORIES:
            if c.lower() in raw.lower():
                return ClassifyResult(c, 0.4)
        return ClassifyResult(cfg.DEFAULT_CATEGORY, 0.0)

    except Exception as e:
        logger.error(prefix + "classify: " + str(e))
        return ClassifyResult(cfg.DEFAULT_CATEGORY, 0.0)