import json
import logging
import os
import time as _time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── LLM 配置 ────
LLM_API_URL = None
LLM_API_KEY = None
DEFAULT_MODEL = "qianfan-code-latest"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TIMEOUT = 300
LLM_RETRY_DELAY = 15

FALLBACK_API_URL = None
FALLBACK_API_KEY = None
FALLBACK_MODEL = "deepseek-v4-flash"


def _get_config_dir():
    """返回 config/ 目录（相对于本脚本所在目录）"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")


def _load_llm_config():
    global LLM_API_URL, LLM_API_KEY, DEFAULT_MODEL
    global FALLBACK_API_URL, FALLBACK_API_KEY, FALLBACK_MODEL
    if LLM_API_URL and LLM_API_KEY:
        return

    cfg_path = os.path.join(_get_config_dir(), "llm.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path) as f:
                data = json.load(f)
            primary = data.get("primary", {})
            fallback = data.get("fallback", {})

            if primary.get("api_url") and primary.get("api_key"):
                LLM_API_URL = primary["api_url"]
                LLM_API_KEY = primary["api_key"]
                if primary.get("model"):
                    DEFAULT_MODEL = primary["model"]
                logger.info(f"主用: {LLM_API_URL} | 模型: {DEFAULT_MODEL}")

            if fallback.get("api_url") and fallback.get("api_key"):
                FALLBACK_API_URL = fallback["api_url"]
                FALLBACK_API_KEY = fallback["api_key"]
                if fallback.get("model"):
                    FALLBACK_MODEL = fallback["model"]
                logger.info(f"备选: {FALLBACK_API_URL} | 模型: {FALLBACK_MODEL}")
        except Exception as e:
            logger.warning(f"读取 config/llm.json 失败: {e}")

    if not LLM_API_URL:
        LLM_API_URL = os.environ.get("LLM_API_URL", "https://api.deepseek.com/chat/completions")
        LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
        logger.info(f"使用环境变量 LLM: {LLM_API_URL}")


def call_llm(system_prompt: str, user_prompt: str, model: str = None,
             temperature: float = 0.7, max_tokens: int = None,
             response_format: Optional[dict] = None,
             timeout: int = DEFAULT_TIMEOUT) -> str:
    _load_llm_config()
    api_key = LLM_API_KEY
    api_url = LLM_API_URL
    if model is None:
        model = DEFAULT_MODEL
    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS
    if max_tokens > 8192:
        logger.warning(f"max_tokens {max_tokens} 过大，调整为 8192")
        max_tokens = 8192

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], "temperature": temperature, "max_tokens": max_tokens}
    if response_format:
        payload["response_format"] = response_format

    logger.info(f"LLM 调用: {api_url} | 模型: {model} | max_tokens: {max_tokens}")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429 and attempt < max_retries - 1:
                delay = LLM_RETRY_DELAY * (2 ** attempt)
                logger.warning(f"429 限流，{delay}秒后重试 ({attempt+1}/{max_retries})...")
                _time.sleep(delay)
                continue
            if e.response.status_code == 429 and FALLBACK_API_URL:
                logger.warning(f"千帆限流，切换到备选: {FALLBACK_MODEL}")
                fb_headers = {"Authorization": f"Bearer {FALLBACK_API_KEY}", "Content-Type": "application/json"}
                fb_payload = dict(payload, model=FALLBACK_MODEL)
                try:
                    fb_resp = requests.post(FALLBACK_API_URL, headers=fb_headers, json=fb_payload, timeout=timeout)
                    fb_resp.raise_for_status()
                    return fb_resp.json()["choices"][0]["message"]["content"].strip()
                except Exception as fb_e:
                    logger.error(f"备选也失败: {fb_e}")
                    raise
            logger.error(f"LLM 调用失败: {e}")
            raise
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise


def call_llm_json(*args, **kwargs) -> dict:
    content = call_llm(*args, **kwargs)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        import re
        m = re.search(r'\{.*\}', content, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise ValueError(f"LLM 返回非 JSON: {content[:100]}")


def fetch_webpage_text(url: str) -> str:
    """抓取网页内容并提取可读文本（含 SPA 元信息兜底）"""
    import re
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "charset=" in content_type:
            enc = content_type.split("charset=")[-1].split(";")[0].strip()
            resp.encoding = enc
        elif resp.apparent_encoding:
            resp.encoding = resp.apparent_encoding

        html = resp.text

        # 提取所有 meta 信息（对 SPA 页面至关重要）
        meta_parts = []

        title = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
        if title:
            meta_parts.append(f"网站标题：{title.group(1).strip()}")

        # 所有 name="description"（可能有中/英多个）
        descs = re.findall(
            r'<meta[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']',
            html, re.DOTALL
        )
        if not descs:
            descs = re.findall(
                r'<meta[^>]*content=["\'](.*?)["\'][^>]*name=["\']description["\']',
                html, re.DOTALL
            )
        for i, d in enumerate(descs):
            label = "描述" if i == 0 else f"描述({i+1})"
            meta_parts.append(f"{label}：{d.strip()}")

        og_desc = re.search(
            r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\'](.*?)["\']',
            html, re.DOTALL
        )
        if og_desc:
            meta_parts.append(f"OG描述：{og_desc.group(1).strip()}")

        og_title = re.search(
            r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\'](.*?)["\']',
            html, re.DOTALL
        )
        if og_title:
            meta_parts.append(f"OG标题：{og_title.group(1).strip()}")

        keywords = re.search(
            r'<meta[^>]*name=["\']keywords["\'][^>]*content=["\'](.*?)["\']',
            html, re.DOTALL
        )
        if keywords:
            meta_parts.append(f"关键词：{keywords.group(1).strip()}")

        meta_text = "\n".join(meta_parts)

        # 清理 HTML 取可见文本
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL)
        text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL)
        text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'&[a-zA-Z]+;', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        # SPA 兜底：可见文本太少时用完整 meta 信息
        if len(text) < 200 and meta_text:
            text = meta_text

        text = text[:3000]
        logger.info(f"抓取网页成功: {url} → {len(text)} 字符")
        return text
    except Exception as e:
        logger.warning(f"抓取网页失败: {url} -> {e}")
        return ""


def analyze_product(url: str, name: str = "") -> dict:
    """
    抓取产品页面 → LLM 提取结构化产品信息
    返回: {"product_name", "positioning", "core_features", "characteristics"}
    """
    page_text = fetch_webpage_text(url)

    prompt = f"""根据网页内容，提取这个产品的关键信息，按格式输出JSON。

产品链接：{url}
产品名称参考：{name or "未知"}

网页内容：
{page_text[:2500] if page_text else "（无法抓取）"}

输出格式：
{{
  "product_name": "产品名称",
  "positioning": "产品定位（一句话说明这是做什么的）",
  "core_features": "核心功能说明",
  "characteristics": ["特点1", "特点2", "特点3", "特点4"]
}}
"""
    logger.info(f"分析产品: {url}")
    result = call_llm_json(
        system_prompt="你是一个信息提取专家，从网页内容中提取产品关键信息，不要编造网页中没有的内容。",
        user_prompt=prompt,
        temperature=0.2,
        max_tokens=2048,
    )
    logger.info(f"分析结果: {result.get('product_name', '未知')}")
    return result
