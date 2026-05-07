#!/usr/bin/env python3
"""
Bilibili 评论区获客模块
================================
功能：
  1. 搜索B站视频（按关键词、按播放量排序）
  2. AI 4维评分（热度+互动+时效+质量）
  3. 获取视频评论内容和评论区上下文
  4. LLM 生成自然评论
  5. 通过 MCP/bilibili-api 发表评论
  6. 持久化去重

依赖：
  - bilibili-api-python （已安装）
  - DeepSeek API （DEEPSEEK_API_KEY）
  - B站登录凭证 (bili_credential.json)

用法：
  # 手动指定关键词
  python3 bili_comment_acquisition.py -k "AI工具" -u "https://your-product.com"

  # 自动模式（AI生成关键词 → 搜索 → 评分 → 评论）
  python3 bili_comment_acquisition.py --auto -u "https://your-product.com" -n "产品名"

  # Dry-run 安全测试
  python3 bili_comment_acquisition.py -k "大模型" --dry-run -vv
"""
import argparse
import json
import logging
import os
import random
import sys
import time
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

# ========== 导入B站API和LLM ==========
sys.path.insert(0, str(SKILL_DIR))
from bilibili_api import video, search, comment, Credential
from bilibili_api.comment import CommentResourceType, OrderType

# LLM 模块复用 xhs-adb-publisher 的
from scripts.xhs_llm import call_llm_json, call_llm

# ========== 配置 ==========
DEFAULT_CONFIG = {
    "max_comments_per_run": 5,
    "max_comments_per_day": 20,
    "max_comments_per_hour": 5,
    "base_interval_seconds": 60,
    "active_hours": [8, 23],
    "search": {
        "min_play": 1000,        # 最低播放量
        "min_like_ratio": 0.01,  # 最低点赞/播放比
        "max_comment_count": 500,  # 评论太多竞争激烈，不选
        "result_count": 20,       # 每轮搜索取前N个
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
    """加载B站登录凭证"""
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
        except:
            pass
    return DEFAULT_CONFIG


def load_published() -> dict:
    """加载已评论历史 {bvid: timestamp, ...}"""
    if COMMENTED_FILE.exists():
        return json.loads(COMMENTED_FILE.read_text())
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
        except:
            pass
    # 默认种子关键词
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
    # 活跃时段检查
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
    for bvid, data in records.items():
        if isinstance(data, dict) and "time" in data:
            t = datetime.fromisoformat(data["time"])
            if t.hour == now.hour and t.date() == now.date():
                count += 1
    return count


def get_daily_count(records: dict) -> int:
    """获取今日已评论数"""
    now = datetime.now().date()
    count = 0
    for bvid, data in records.items():
        if isinstance(data, dict) and "time" in data:
            t = datetime.fromisoformat(data["time"])
            if t.date() == now:
                count += 1
    return count


# ========== 核心功能 ==========

async def search_videos(keyword: str, limit: int = 20, order: str = "click") -> list:
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

    logger.info(f"🔍 搜索关键词: '{keyword}' 排序:{order} 取{limit}条")

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
        play = item.get("play", 0) or 0
        review = item.get("review", 0) or 0
        danmaku = item.get("video_review", 0) or 0

        videos.append({
            "bvid": item.get("bvid", ""),
            "aid": item.get("aid", 0),
            "title": title,
            "author": item.get("author", ""),
            "play": int(play),
            "review": int(review),
            "danmaku": int(danmaku),
            "duration": item.get("duration", ""),
            "pic": item.get("pic", ""),
            "description": item.get("description", "")[:300],
        })

    logger.info(f"  搜索到 {len(videos)} 个视频")
    return videos


def score_video(v: dict, config: dict) -> float:
    """
    AI 评分（简化版——基于搜索元数据初步筛选）
    返回 0-100 分，实际最终评分由 LLM 做

    这里做初步过滤：
    - play > min_play
    - like_ratio > min_like_ratio（无like用play/review估算）
    - review < max_comment_count（评论太多竞争激烈）
    """
    rules = config["search"]
    play = v["play"]
    review = v["review"]

    if play < rules["min_play"]:
        return 0
    if review > rules["max_comment_count"]:
        return 0

    # 互动率 = 评论数/播放量
    interact_rate = review / max(play, 1)
    # 综合分数：播放量 + 互动率 + 随机因子
    score = (
        0.4 * min(play / 50000, 1) * 100 +
        0.4 * min(interact_rate * 100, 1) * 100 +
        0.2 * random.uniform(60, 100)
    )
    return score


async def get_video_detail(v: dict) -> dict:
    """
    获取视频详情（播放量、点赞数、评论数、收藏数等）
    用于LLM评分和评论生成
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
            "tname": info.get("tname", ""),  # 分区
            "desc": info.get("desc", "")[:500],
        })
    except Exception as e:
        logger.warning(f"  获取视频详情失败 {v.get('bvid', '')}: {e}")
    return v


def llm_score_video(v: dict, product_url: str, product_name: str) -> float:
    """
    LLM 4维评分视频的获客价值
    返回 0-100 分
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
        result = call_llm_json(
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


def llm_generate_comment(video_info: dict, product_url: str, product_name: str) -> str:
    """
    LLM 根据视频内容生成自然评论

    Args:
        video_info: 视频详情
        product_url: 产品链接
        product_name: 产品名称

    Returns:
        生成的评论文字
    """
    # 获取评论区前几条评论作为风格参考
    top_comments_text = ""
    if video_info.get("sample_comments"):
        top_comments_text = "\n".join([
            f"  [{c.get('uname','')}]: {c.get('content','')}"
            for c in video_info.get("sample_comments", [])[:5]
        ])

    prompt = f"""你是一个真实的B站用户，正在看一个视频的评论区。

## 视频信息
- 标题: {video_info.get('title', '')}
- UP主: {video_info.get('author', '')}
- 分区: {video_info.get('tname', '')}
- 简介: {video_info.get('desc', '')[:300]}

## 评论区风格参考（前几条评论）
{top_comments_text or '（暂无参考）'}

## 产品链接（必须自然融入正文中间，像分享经历附带链接）
- 名称: {product_name}
- 链接: {product_url}

{COMMENT_STYLES}

## 输出要求
- 只输出评论内容本身
- 如果视频内容与产品相关，把链接自然放在正文中间："我之前用过XX(链接)，感觉…"
- 如果视频内容与产品不相关，可以只发表普通的评论（不植入）
- 不要提"值得一提的是"、"总之"这种营销感强的词
- 长度15-60字
"""
    try:
        text = call_llm(
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
        success = result.get("rp_id") is not None or result.get("success", False)
        if success:
            logger.info(f"  ✅ 评论发表成功")
        else:
            logger.warning(f"  评论发表返回异常: {result}")
        return success
    except Exception as e:
        logger.error(f"  ❌ 评论发表失败: {e}")
        return False


def jitter_sleep(base_sec: float):
    """带抖动的等待"""
    jitter = base_sec * random.uniform(0.8, 1.5)
    logger.info(f"  等待 {jitter:.0f} 秒...")
    time.sleep(jitter)


# ========== AI生成关键词 ==========

def ai_generate_keywords(product_url: str, product_name: str, seed_keywords: list) -> list:
    """
    AI根据产品信息生成搜索关键词
    """
    seed_str = "\n".join(f"  - {kw}" for kw in seed_keywords[:5])
    prompt = f"""你是一个B站关键词策略师。根据以下产品信息，生成10个B站搜索关键词。

用这些关键词去B站搜索相关视频，目的是找到适合做评论区获客的内容。

## 产品信息
- 名称: {product_name}
- 链接: {product_url}

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
        result = call_llm_json(
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

async def run(keyword: str = None, product_url: str = "", product_name: str = "",
              auto: bool = False, dry_run: bool = False, max_comments: int = 5,
              verbose: bool = False) -> dict:
    """
    完整获客流程：搜索 → 评分 → 评论

    Args:
        keyword: 搜索关键词
        product_url: 产品链接
        product_name: 产品名称
        auto: 自动模式（AI生成关键词）
        dry_run: 仅测试不发真实评论
        max_comments: 本运行最多评论数
        verbose: 详细输出

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
    cred = load_credential()

    if not dry_run and not cred:
        logger.error("❌ 需要B站登录凭证才可发表评论！请先运行扫码登录")
        return {"status": "failed", "error": "no_credential"}

    if not dry_run and cred:
        logger.info(f"✅ B站账号已登录")

    # 已有评论历史（去重）
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
        keywords = ai_generate_keywords(product_url, product_name, load_seed_keywords())
    elif keyword:
        keywords = [keyword]
    else:
        keywords = random.sample(load_seed_keywords(), 3)

    logger.info(f"搜索关键词: {keywords}")
    print()

    # 步骤2: 搜索视频
    logger.info("步骤2/5: 搜索B站视频...")
    all_videos = []
    for kw in keywords:
        videos = await search_videos(kw, limit=config["search"]["result_count"])
        all_videos.extend(videos)
        jitter_sleep(1)  # 搜索间隔防封

    # 去重（按bvid）
    seen = set()
    unique_videos = []
    for v in all_videos:
        if v["bvid"] and v["bvid"] not in seen:
            seen.add(v["bvid"])
            unique_videos.append(v)

    # 过滤掉已评过的
    uncommented = [v for v in unique_videos if v["bvid"] not in published]
    logger.info(f"共 {len(unique_videos)} 个视频（已评 {len(unique_videos) - len(uncommented)} 个）")
    print()

    if not uncommented:
        logger.info("没有新的视频可评论")
        return {"status": "no_new_videos", "total": 0}

    # 步骤3: 获取详情 + 评分
    logger.info("步骤3/5: 评分筛选...")
    scored_videos = []
    for v in uncommented[:config["search"]["result_count"]]:
        # 初步过滤
        score = score_video(v, config)
        if score < 30:
            continue
        # 获取详情
        v = await get_video_detail(v)
        v["sample_comments"] = await get_sample_comments(v.get("aid", 0), cred)
        # LLM评分
        llm_score = llm_score_video(v, product_url, product_name)
        v["llm_score"] = llm_score
        scored_videos.append(v)
        jitter_sleep(0.5)

    # 按LLM评分排序
    scored_videos.sort(key=lambda x: x.get("llm_score", 0), reverse=True)
    top_videos = scored_videos[:max_comments]

    logger.info(f"\n🔝 获客潜力TOP {len(top_videos)}:")
    for i, v in enumerate(top_videos, 1):
        logger.info(f"  {i}. [{v['llm_score']:.0f}分] {v['title'][:40]} @{v['author']} (播放{v['play']})")
    print()

    # 步骤4: 生成评论
    logger.info("步骤4/5: 生成评论...")
    comments = []
    for v in top_videos:
        text = llm_generate_comment(v, product_url, product_name)
        if text:
            comments.append({"video": v, "comment": text})
            logger.info(f"  评论: {text[:60]}...")
        jitter_sleep(0.5)
    print()

    # 步骤5: 发表评论
    logger.info("步骤5/5: 发表评论...")

    results = []
    for item in comments:
        v = item["video"]
        text = item["comment"]
        bvid = v["bvid"]

        logger.info(f"▶️ [{bvid}] {v['title'][:30]}")
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

        # 评论间隔（防风控）
        if len(results) < len(comments):
            interval = config["base_interval_seconds"] * random.uniform(0.8, 1.5)
            logger.info(f"  等待 {interval:.0f} 秒后下一条...")
            time.sleep(interval)

    # 汇总
    sent = [r for r in results if r.get("status") == "sent"]
    failed = [r for r in results if r.get("status") == "failed"]
    dry_run_results = [r for r in results if r.get("status") == "dry_run"]

    print()
    logger.info("========== 运行结果 ==========")
    logger.info(f"✅ 发送: {len(sent)}  ❌ 失败: {len(failed)}  🔍 Dry-Run: {len(dry_run_results)}")
    for r in failed:
        logger.warning(f"  ❌ {r['bvid']}: {r.get('comment', '')[:30]}")

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
    parser.add_argument("-k", "--keyword", help="搜索关键词")
    parser.add_argument("-u", "--product-url", default=os.environ.get("BILI_PRODUCT_URL", ""),
                        help="产品链接")
    parser.add_argument("-n", "--product-name", default=os.environ.get("BILI_PRODUCT_NAME", ""),
                        help="产品名称")
    parser.add_argument("--auto", action="store_true", help="AI自动模式")
    parser.add_argument("--dry-run", action="store_true", help="仅测试不发表")
    parser.add_argument("--max-comments", "-m", type=int, default=5, help="本运行最多评论数")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="详细输出")

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
            verbose=args.verbose > 0,
        )
    )


if __name__ == "__main__":
    import asyncio
    main()
