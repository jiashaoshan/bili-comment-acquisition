#!/usr/bin/env python3
"""
Bilibili 评论区获客模块
================================
功能：
  1. 搜索B站视频（按关键词、按播放量排序）
  2. AI 4维评分（热度+互动+时效+质量）
  3. 获取视频评论内容和评论区上下文
  4. LLM 生成自然评论
  5. 通过 bilibili-api 发表评论
  6. 持久化去重

依赖：
  - bilibili-api-python
  - DeepSeek API（DEEPSEEK_API_KEY 或 openclaw.json 配置）
  - B站登录凭证（bili_credential.json）

用法：
  # 手动指定关键词
  python3 bili_comment_acquisition.py -k "AI工具" -u "https://your-product.com"

  # 自动模式（AI生成关键词 → 搜索 → 评分 → 评论）
  python3 bili_comment_acquisition.py --auto -u "https://your-product.com" -n "产品名"

  # Dry-run 安全测试
  python3 bili_comment_acquisition.py -k "大模型" --dry-run -vv
"""
import argparse
import asyncio
import json
import logging
import os
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ========== 路径设置 ==========
SKILL_DIR = Path(__file__).parent.absolute()
DATA_DIR = SKILL_DIR / "data"
CONFIG_DIR = SKILL_DIR / "config"
DATA_DIR.mkdir(parents=True, exist_ok=True)

COMMENTED_FILE = DATA_DIR / "bili-commented-history.json"
SEED_KEYWORDS_FILE = CONFIG_DIR / "keywords.json"

# ========== 日志 ==========
log_file = DATA_DIR / f"bili_acq_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(str(log_file), encoding="utf-8")],
)
logger = logging.getLogger("bili-acq")

# 自动清理 7 天前的日志文件
try:
    now = datetime.now()
    for f in DATA_DIR.glob("bili_acq_*.log"):
        date_str = f.stem.split("_")[-1]
        file_date = datetime.strptime(date_str, "%Y%m%d")
        if (now - file_date).days > 7:
            f.unlink()
except (ValueError, OSError):
    pass

# ========== 导入B站API ==========
from bilibili_api import video, search, comment, Credential
from bilibili_api.comment import CommentResourceType, OrderType

# ========== LLM 模块（本地独立，无跨技能依赖） ==========
from bili_llm import call_llm_json, call_llm, analyze_product

# ========== 配置 ==========
DEFAULT_CONFIG = {
    "max_comments_per_run": 5,
    "max_comments_per_day": 20,
    "max_comments_per_hour": 5,
    "base_interval_seconds": 60,
    "active_hours": [8, 23],
    "search": {
        "min_play": 100,         # 最低播放量
        "min_like_ratio": 0.01,  # 最低点赞/播放比
        "max_comment_count": 500,  # 评论太多竞争激烈，不选
        "result_count": 20,       # 每轮搜索取前N个
        "min_review": 2,          # 最少评论数（没人评论的视频不去）
    },
    "scoring": {
        "hot_weight": 0.35,
        "interact_weight": 0.35,
        "fresh_weight": 0.20,
        "quality_weight": 0.10,
    },
}

# ========== 评论风格 ==========
COMMENT_STYLES = """
## 评论风格（AI自行选择最合适的）

| 风格 | 说明 | 适用场景 |
|------|------|----------|
| 赞同共鸣型 | 对视频内容表示强烈认同，引发情感连接 | 干货教程、观点类 |
| 补充分享型 | 补充自己的相关经验，自然植入产品 | 经验分享、评测类 |
| 提问互动型 | 提出开放性问题，引导UP回复 | 教程、科普类 |
| 经验交流型 | 分享自身经历，建立平等交流 | 踩坑、对比类 |

## 核心要求
- 语气自然口语化，像真人用户的随口评论
- 不直接发广告链接
- 不要复制照抄视频文案
- 长度15-60字
- 如果植入产品信息，要以"我之前用过XX，感觉…"这种分享的口吻
"""


# ========== 辅助函数 ==========

def load_credential() -> Optional[Credential]:
    """加载B站登录凭证

    优先级: 环境变量 > bili_credential.json
    环境变量: BILIBILI_SESSDATA, BILIBILI_BILI_JCT, BILIBILI_BUVID3, BILIBILI_DEDEUSERID
    """
    sessdata = os.environ.get("BILIBILI_SESSDATA") or ""
    bili_jct = os.environ.get("BILIBILI_BILI_JCT") or ""
    buvid3 = os.environ.get("BILIBILI_BUVID3") or ""
    dedeuserid = os.environ.get("BILIBILI_DEDEUSERID") or ""

    if sessdata and bili_jct:
        return Credential(
            sessdata=sessdata,
            bili_jct=bili_jct,
            buvid3=buvid3 or "",
            dedeuserid=dedeuserid or "",
        )

    cred_file = SKILL_DIR / "bili_credential.json"
    if not cred_file.exists():
        return None
    with open(cred_file) as f:
        data = json.load(f)
    return Credential(
        sessdata=data.get("sessdata", ""),
        bili_jct=data.get("bili_jct", ""),
        buvid3=data.get("buvid3") or "",
        dedeuserid=data.get("dedeuserid", ""),
    )


def load_config() -> dict:
    """加载发布配置"""
    fp = CONFIG_DIR / "publish.json"
    if fp.exists():
        try:
            return json.loads(fp.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    return DEFAULT_CONFIG


def load_published() -> dict:
    """加载已评论历史 {bvid: timestamp, ...}"""
    if COMMENTED_FILE.exists():
        try:
            return json.loads(COMMENTED_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_published(bvid: str, comment_text: str):
    """保存评论记录"""
    records = load_published()
    records[bvid] = {
        "bvid": bvid,
        "comment": comment_text[:100],
        "time": datetime.now().isoformat(),
    }
    COMMENTED_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2))


def load_seed_keywords() -> list:
    """加载种子关键词"""
    if SEED_KEYWORDS_FILE.exists():
        try:
            return json.loads(SEED_KEYWORDS_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    return [
        "AI工具", "效率工具", "数据分析", "Python教程",
        "AI编程", "大模型", "自动化办公", "程序员效率",
    ]


def rate_limit_check(config: dict, today_count: int, hour_count: int) -> bool:
    """检查是否在限制内"""
    if today_count >= config["max_comments_per_day"]:
        logger.warning(f"今日评论已达上限 ({today_count}/{config['max_comments_per_day']})")
        return False
    if hour_count >= config["max_comments_per_hour"]:
        logger.warning(f"本小时评论已达上限 ({hour_count}/{config['max_comments_per_hour']})")
        return False
    hour = datetime.now().hour
    active = config["active_hours"]
    if not (active[0] <= hour < active[1]):
        logger.info(f"当前不在活跃时段 ({active[0]}:00-{active[1]}:00)")
        return False
    return True


def get_hourly_count(records: dict) -> int:
    """获取本小时已评论数"""
    now = datetime.now()
    count = 0
    for data in records.values():
        if isinstance(data, dict) and "time" in data:
            t = datetime.fromisoformat(data["time"])
            if t.hour == now.hour and t.date() == now.date():
                count += 1
    return count


def get_daily_count(records: dict) -> int:
    """获取今日已评论数"""
    today = datetime.now().date()
    count = 0
    for data in records.values():
        if isinstance(data, dict) and "time" in data:
            t = datetime.fromisoformat(data["time"])
            if t.date() == today:
                count += 1
    return count


# ========== 核心功能 ==========

async def search_videos(keyword: str, limit: int = 20, order: str = "totalrank") -> list:
    """
    搜索B站视频，按播放量/最新排序

    Args:
        keyword: 搜索关键词
        limit: 返回数量
        order: 排序 click=播放量 pubdate=最新 dm=弹幕 totalrank=综合

    Returns:
        视频列表 [{bvid, title, author, play, review, danmaku, aid, description}, ...]
    """
    order_map = {
        "totalrank": search.OrderVideo.TOTALRANK,
        "click": search.OrderVideo.CLICK,
        "pubdate": search.OrderVideo.PUBDATE,
        "dm": search.OrderVideo.DM,
    }
    order_enum = order_map.get(order, search.OrderVideo.CLICK)

    logger.info(f"搜索关键词: '{keyword}' 排序:{order} 取{limit}条")

    result = await search.search_by_type(
        keyword=keyword,
        search_type=search.SearchObjectType.VIDEO,
        page=1,
        order_type=order_enum,
        page_size=limit,
    )

    videos = []
    for item in result.get("result", [])[:limit]:
        title = item.get("title", "").replace('<em class="keyword">', "").replace("</em>", "")
        videos.append({
            "bvid": item.get("bvid", ""),
            "aid": item.get("aid", 0),
            "title": title,
            "author": item.get("author", ""),
            "play": int(item.get("play", 0) or 0),
            "review": int(item.get("review", 0) or 0),
            "danmaku": int(item.get("video_review", 0) or 0),
            "duration": item.get("duration", ""),
            "pic": item.get("pic", ""),
            "description": item.get("description", "")[:300],
        })

    logger.info(f"  搜索到 {len(videos)} 个视频")
    return videos


def score_video(v: dict, config: dict) -> float:
    """
    基于搜索元数据初步评分筛选
    返回 0-100 分，0 分表示不通过
    """
    rules = config["search"]
    play = v.get("play", 0)
    review = v.get("review", 0)

    if play < rules["min_play"]:
        return 0
    if review < rules.get("min_review", 0):
        return 0
    if review > rules["max_comment_count"]:
        return 0

    interact_rate = review / max(play, 1)
    score = (
        0.5 * min(play / 50000, 1) * 100 +
        0.5 * min(interact_rate * 100, 1) * 100
    )
    return score


async def get_video_detail(v: dict) -> dict:
    """
    获取视频详情（播放量、点赞数、评论数、收藏数等）
    """
    cred = load_credential()
    try:
        obj = video.Video(bvid=v["bvid"], credential=cred) if cred else video.Video(bvid=v["bvid"])
        info = await obj.get_info()
        v.update({
            "like": info.get("stat", {}).get("like", 0),
            "coin": info.get("stat", {}).get("coin", 0),
            "favorite": info.get("stat", {}).get("favorite", 0),
            "share": info.get("stat", {}).get("share", 0),
            "view": info.get("stat", {}).get("view", 0),
            "pubdate": datetime.fromtimestamp(info.get("pubdate", 0)).isoformat(),
            "tname": info.get("tname", ""),
            "desc": info.get("desc", "")[:500],
        })
    except Exception as e:
        logger.warning(f"  获取视频详情失败 {v.get('bvid', '')}: {e}")
    return v


async def llm_score_video(v: dict, product_url: str, product_name: str,
                           product_info: Optional[dict] = None) -> float:
    """
    LLM 4维评分视频的获客价值
    返回 0-100 分
    """
    info_section = ""
    if product_info:
        info_section = f"""
- 定位: {product_info.get('positioning', '')}

"""

    prompt = f"""你是一个社交媒体获客分析师。请评估下面这个B站视频的"评论区植入推广"价值。

## 视频信息
- 标题: {v.get('title', '')}
- UP主: {v.get('author', '')}
- 播放量: {v.get('view', v.get('play', 0))}
- 点赞: {v.get('like', 0)}
- 评论数: {v.get('review', 0)}
- 收藏: {v.get('favorite', 0)}
- 转发: {v.get('share', 0)}
- 分区: {v.get('tname', '')}
- 发布时间: {v.get('pubdate', '')}
- 简介: {v.get('desc', '')[:300]}

## 推广产品
- 名称: {product_name}
- 链接: {product_url}
{info_section}
## 评分维度（每项0-25分）
1. 热度价值（25分）：播放量高、点赞多，说明曝光量大
2. 互动潜力（25分）：评论数/播放比高、评论区活跃，愿意看评论
3. 内容契合度（25分）：视频内容与产品的目标用户群匹配程度
4. 评论留人空间（25分）：该视频评论区风格是否适合自然植入软广

## 输出格式
纯JSON：
{{
  "score": <0-100的总分>,
  "hot_score": <0-25>,
  "interact_score": <0-25>,
  "fit_score": <0-25>,
  "space_score": <0-25>,
  "reason": "<一句话理由>"
}}
"""
    try:
        result = await asyncio.to_thread(
            call_llm_json,
            system_prompt="你是一个专业的评论区获客分析师。严格按照JSON格式输出。",
            user_prompt=prompt,
            max_tokens=1024,
        )
        score = result.get("score", 0)
        logger.info(f"  评分: {score:.1f} | {result.get('reason', '')}")
        return score
    except Exception as e:
        logger.warning(f"  LLM评分失败: {e}")
        return 0


async def llm_generate_comment(video_info: dict, product_url: str, product_name: str,
                                product_info: Optional[dict] = None) -> str:
    """
    LLM 根据视频内容生成自然评论
    """
    top_comments_text = ""
    if video_info.get("sample_comments"):
        top_comments_text = "\n".join([
            f"  [{c.get('uname','')}]: {c.get('content','')}"
            for c in video_info.get("sample_comments", [])[:5]
        ])

    product_detail = f"- 名称: {product_name}\n- 官网: {product_url}"
    if product_info:
        product_detail += f"\n- 一句话简介: {product_info.get('positioning', '')}"

    prompt = f"""你是一个真实的B站用户，正在看一个视频的评论区。

## 视频信息
- 标题: {video_info.get('title', '')}
- UP主: {video_info.get('author', '')}
- 分区: {video_info.get('tname', '')}
- 简介: {video_info.get('desc', '')[:300]}

## 评论区风格参考（前几条评论）
{top_comments_text or '（暂无参考）'}

## 你的产品（如果合适可以自然提及）
{product_detail}

{COMMENT_STYLES}

## 输出要求
- 只输出评论内容本身，不要有其他文字
- 不要提"值得一提的是"、"总之"这种营销感强的词
- 每条评论都必须以第一人称自然提及产品名和官网链接，可用"我之前用过"、"最近在折腾"、"在 ai.interwestinfo.com 上发现的"这种口吻
- 长度15-80字
"""
    try:
        text = await asyncio.to_thread(
            call_llm,
            system_prompt="你是一个B站资深用户，喜欢在评论区互动。只输出评论内容，不要加JSON外壳。",
            user_prompt=prompt,
            max_tokens=512,
        )
        return text.strip().strip('"').strip("'")
    except Exception as e:
        logger.warning(f"  评论生成失败: {e}")
        return ""


async def get_sample_comments(aid: int, cred: Credential = None, limit: int = 10) -> list:
    """获取视频前几条评论作为风格参考"""
    try:
        resp = await comment.get_comments(
            oid=aid,
            type_=CommentResourceType.VIDEO,
            page_index=1,
            order=OrderType.LIKE,
            credential=cred,
        )
        comments = []
        for c in resp.get("replies", [])[:limit]:
            comments.append({
                "uname": c.get("member", {}).get("uname", ""),
                "content": c.get("content", {}).get("message", ""),
                "like": c.get("like", 0),
            })
        return comments
    except Exception as e:
        logger.warning(f"  获取样评论失败: {e}")
        return []


async def send_comment_text(aid: int, text: str, cred: Credential) -> bool:
    """
    发表评论

    Args:
        aid: 视频aid
        text: 评论内容
        cred: B站凭证

    Returns:
        是否成功
    """
    try:
        result = await comment.send_comment(
            text=text,
            oid=aid,
            type_=CommentResourceType.VIDEO,
            credential=cred,
        )
        success = result.get("rpid") is not None
        if success:
            logger.info(f"  评论发表成功")
        else:
            logger.warning(f"  评论发表返回异常: {result}")
        return success
    except Exception as e:
        logger.error(f"  评论发表失败: {e}")
        return False


async def jitter_sleep(base_sec: float):
    """带抖动的异步等待"""
    jitter = base_sec * random.uniform(0.8, 1.5)
    logger.info(f"  等待 {jitter:.0f} 秒...")
    await asyncio.sleep(jitter)


# ========== AI生成关键词 ==========

async def ai_generate_keywords(product_url: str, product_name: str, seed_keywords: list,
                                product_info: Optional[dict] = None) -> list:
    """
    AI根据产品信息生成搜索关键词
    """
    info_section = f"- 名称: {product_name}\n- 链接: {product_url}"
    if product_info:
        info_section += f"\n- 定位: {product_info.get('positioning', '')}"

    seed_str = "\n".join(f"  - {kw}" for kw in seed_keywords[:5])
    prompt = f"""你是一个B站关键词策略师。根据以下产品信息，生成10个B站搜索关键词。

用这些关键词去B站搜索相关视频，目的是找到适合做评论区获客的内容。

## 产品信息
{info_section}

## 种子关键词（参考）
{seed_str}

## 要求
- 关键词要偏B站用户常用的搜索词
- 覆盖：教程类、测评类、经验分享类
- 不要太宽泛也不要太冷门

## 输出格式
纯JSON数组：
["关键词1", "关键词2", ...]
"""
    try:
        result = await asyncio.to_thread(
            call_llm_json,
            system_prompt="你是一个B站关键词策略师。输出JSON数组。",
            user_prompt=prompt,
            max_tokens=1024,
        )
        if isinstance(result, list):
            keywords = result[:10]
        elif isinstance(result, dict):
            keywords = result.get("keywords", result.get("keyword", []))[:10]
        else:
            keywords = seed_keywords[:5]
        logger.info(f"AI生成关键词: {keywords}")
        return keywords
    except Exception as e:
        logger.warning(f"关键词生成失败: {e}")
        return seed_keywords[:5]


# ========== 主流程 ==========

async def process_single_video(v: dict, config: dict, cred: Optional[Credential],
                                product_url: str, product_name: str,
                                product_info: Optional[dict] = None) -> Optional[dict]:
    """处理单个视频：初步评分 → 获取详情 → 评论区样本 → LLM评分"""
    if score_video(v, config) < 30:
        return None
    v = await get_video_detail(v)
    v["sample_comments"] = await get_sample_comments(v.get("aid", 0), cred)
    v["llm_score"] = await llm_score_video(v, product_url, product_name, product_info)
    return v


async def run(keyword: list = None, product_url: str = "", product_name: str = "",
              auto: bool = False, dry_run: bool = False, max_comments: Optional[int] = None,
              verbose: int = 0) -> dict:
    """
    完整获客流程：搜索 → 评分 → 评论

    Args:
        keyword: 搜索关键词
        product_url: 产品链接
        product_name: 产品名称
        auto: 自动模式（AI生成关键词）
        dry_run: 仅测试不发真实评论
        max_comments: 本运行最多评论数
        verbose: 详细输出级别

    Returns:
        运行结果
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    print()
    print("  ╔════════════════════════════════════════╗")
    print("  ║   Bilibili 评论区获客                  ║")
    print("  ║   搜索 → AI评分 → 自然评论 → 发表      ║")
    print("  ╚════════════════════════════════════════╝")
    print()

    logger.info(f"产品: {product_name or product_url}")
    logger.info(f"模式: {'Dry-Run(模拟)' if dry_run else '正式运行'}")

    config = load_config()
    if max_comments is None:
        max_comments = config.get("max_comments_per_run", 5)

    # 真实抓取产品页面，提取结构化信息
    logger.info("分析产品页面...")
    product_info = await asyncio.to_thread(
        analyze_product, product_url, product_name
    )
    product_name = product_info.get("product_name", product_name)
    logger.info(f"产品名称: {product_name}")
    logger.info(f"产品定位: {product_info.get('positioning', '')}")

    cred = load_credential()

    if not dry_run and not cred:
        logger.error("需要B站登录凭证才可发表评论！请先运行扫码登录")
        return {"status": "failed", "error": "no_credential"}

    if not dry_run and cred:
        logger.info("B站账号已登录")

    published = load_published()
    daily_count = get_daily_count(published)
    hourly_count = get_hourly_count(published)
    logger.info(f"今日已评: {daily_count} 本小时: {hourly_count}")

    if not dry_run and not rate_limit_check(config, daily_count, hourly_count):
        return {"status": "rate_limited", "daily": daily_count, "hourly": hourly_count}

    # 步骤1: 确定关键词
    keywords = []
    if auto:
        logger.info("步骤1/5: AI生成关键词...")
        keywords = await ai_generate_keywords(product_url, product_name, load_seed_keywords(), product_info)
    elif keyword:
        keywords = keyword
    else:
        keywords = random.sample(load_seed_keywords(), 3)

    logger.info(f"搜索关键词: {keywords}")
    print()

    # 步骤2: 搜索视频
    logger.info("步骤2/5: 搜索B站视频...")
    all_videos = []
    for kw in keywords:
        for sort_order in ("click", "pubdate"):
            videos = await search_videos(kw, limit=config["search"]["result_count"], order=sort_order)
            all_videos.extend(videos)
        await jitter_sleep(1)

    # 去重（按bvid）
    seen = set()
    unique_videos = []
    for v in all_videos:
        if v["bvid"] and v["bvid"] not in seen:
            seen.add(v["bvid"])
            unique_videos.append(v)

    uncommented = [v for v in unique_videos if v["bvid"] not in published]
    logger.info(f"共 {len(unique_videos)} 个视频（已评 {len(unique_videos) - len(uncommented)} 个）")
    print()

    if not uncommented:
        logger.info("没有新的视频可评论")
        return {"status": "no_new_videos", "total": 0}

    # 步骤3: 并行获取详情 + 评分
    logger.info("步骤3/5: 评分筛选...")

    sem = asyncio.Semaphore(5)

    async def process_with_sem(v):
        async with sem:
            return await process_single_video(v, config, cred, product_url, product_name, product_info)

    tasks = [process_with_sem(v) for v in uncommented[:config["search"]["result_count"]]]
    results = await asyncio.gather(*tasks)
    scored_videos = [r for r in results if r is not None and r.get("llm_score", 0) > 0]

    # 时效性衰减：pubdate越近评分加成，半年以上视频打折
    import time
    now_ts = time.time()
    for v in scored_videos:
        pub = v.get("pubdate", "")
        if pub:
            try:
                pub_ts = datetime.fromisoformat(pub).timestamp()
                days_old = (now_ts - pub_ts) / 86400
                # 30天内无衰减，之后线性降至30%最低
                freshness = max(0.3, 1.0 - max(0, (days_old - 30)) / 180)
                v["_final_score"] = v.get("llm_score", 0) * freshness
            except:
                v["_final_score"] = v.get("llm_score", 0)
        else:
            v["_final_score"] = v.get("llm_score", 0)

    # 按综合评分排序（LLM评分 × 时效性因子）
    scored_videos.sort(key=lambda x: x.get("_final_score", 0), reverse=True)
    top_videos = scored_videos[:max_comments]

    logger.info(f"\n获客潜力TOP {len(top_videos)} (时效调整后):")
    for i, v in enumerate(top_videos, 1):
        decay = v.get('_final_score', 0) / max(v.get('llm_score', 1), 1)
        logger.info(f"  {i}. [{v['_final_score']:.0f}分] {v['title'][:40]} @{v['author']} (播放{v['play']}) 时效{decay:.2f}")
    print()

    # 步骤4: 生成评论
    logger.info("步骤4/5: 生成评论...")
    comments = []
    for v in top_videos:
        text = await llm_generate_comment(v, product_url, product_name, product_info)
        if text:
            comments.append({"video": v, "comment": text})
            logger.info(f"  评论: {text[:60]}...")
    print()

    # 步骤5: 发表评论
    logger.info("步骤5/5: 发表评论...")

    results = []
    for item in comments:
        v = item["video"]
        text = item["comment"]
        bvid = v["bvid"]

        logger.info(f"[{bvid}] {v['title'][:30]}")
        logger.info(f"   评论: {text}")

        if dry_run:
            logger.info(f"   (dry-run, 跳过发表)")
            results.append({
                "bvid": bvid, "title": v["title"][:40],
                "comment": text, "status": "dry_run",
            })
            continue

        success = await send_comment_text(v["aid"], text, cred)
        status = "sent" if success else "failed"
        results.append({
            "bvid": bvid, "title": v["title"][:40],
            "comment": text, "status": status,
        })

        if success:
            save_published(bvid, text)

        if len(results) < len(comments):
            interval = config["base_interval_seconds"] * random.uniform(0.8, 1.5)
            logger.info(f"  等待 {interval:.0f} 秒后下一条...")
            await asyncio.sleep(interval)

    sent = [r for r in results if r.get("status") == "sent"]
    failed = [r for r in results if r.get("status") == "failed"]
    dry_run_results = [r for r in results if r.get("status") == "dry_run"]

    print()
    logger.info("========== 运行结果 ==========")
    logger.info(f"发送: {len(sent)}  失败: {len(failed)}  Dry-Run: {len(dry_run_results)}")
    for r in failed:
        logger.warning(f"  {r['bvid']}: {r.get('comment', '')[:30]}")

    return {
        "status": "completed",
        "total_videos": len(uncommented),
        "sent": len(sent),
        "failed": len(failed),
        "dry_run": len(dry_run_results),
        "results": results,
        "keywords": keywords,
    }


def main():
    parser = argparse.ArgumentParser(description="Bilibili 评论区获客")
    parser.add_argument("-k", "--keyword", nargs="+", default=[],
                        help="搜索关键词（可指定多个，如 -k AI工具 效率工具）")
    parser.add_argument("-u", "--product-url", default=os.environ.get("BILI_PRODUCT_URL", ""),
                        help="产品链接")
    parser.add_argument("-n", "--product-name", default=os.environ.get("BILI_PRODUCT_NAME", ""),
                        help="产品名称")
    parser.add_argument("--auto", action="store_true", help="AI自动模式")
    parser.add_argument("--dry-run", action="store_true", help="仅测试不发表")
    parser.add_argument("--max-comments", "-m", type=int, default=None, help="本运行最多评论数（默认从配置文件读取）")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="详细输出（-v=DEBUG, -vv=DEBUG+）")

    args = parser.parse_args()

    if not args.product_url:
        print("错误: 请提供产品链接 (-u/--product-url 或 BILI_PRODUCT_URL 环境变量)")
        sys.exit(1)

    asyncio.run(
        run(
            keyword=args.keyword,
            product_url=args.product_url,
            product_name=args.product_name,
            auto=args.auto,
            dry_run=args.dry_run,
            max_comments=args.max_comments,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
