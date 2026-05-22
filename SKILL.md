---
name: Bili Comment Acquisition
description: |
  B站评论区获客技能。搜索B站视频 → AI 4维评分 → LLM生成自然评论 → 自动发表。
  基于 bilibili-api-python + 千帆/DeepSeek API，支持 SPA 产品页面抓取。
metadata:
  openclaw:
    emoji: "🎯"
    requires:
      env: ["DEEPSEEK_API_KEY"]
      config: ["config/llm.json"]
    category: "acquisition"
    tags: ["bilibili", "comment-acquisition", "marketing", "ai"]
---

# B站评论区获客技能

## 流程

```
[0. 产品分析] → 真实抓取产品页面 → LLM提取结构化产品信息
      ↓
[1. 关键词] → AI生成 / 指定搜索关键词（同时搜播放量+最新排序）
      ↓
[2. 搜索] → B站API搜索视频，click+pubdate双排序合并去重
      ↓
[3. 评分] → 初步过滤（播放量>100，评论数2-500）→ LLM 4维评分
      ↓
[4. 详情] → 获取视频详情 + 评论区前几条热评作为风格参考
      ↓
[5. 生成评论] → LLM生成B站风格的互动评论，含产品名+链接
      ↓
[6. 发表] → B站API发表评论（带抖动延迟防封）
      ↓
[7. 记录] → JSON去重，防重复评论
```

## 使用

```bash
# 安装依赖
pip install -r requirements.txt

# 首次扫码登录
python3 bili_login.py

# 指定关键词运行（dry-run安全测试）
python3 bili_comment_acquisition.py -k "AI工具" -u "https://your-product.com" -n "产品名" --dry-run -vv

# AI自动模式
python3 bili_comment_acquisition.py --auto -u "https://your-product.com" -n "产品名"

# 限制评论数
python3 bili_comment_acquisition.py -k "效率工具" -u "https://your-product.com" -m 3
```

## 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-k, --keyword` | 搜索关键词（可指定多个） | 从 `keywords.json` 随机取 |
| `-u, --product-url` | 产品链接（必填） | `BILI_PRODUCT_URL` 环境变量 |
| `-n, --product-name` | 产品名称 | `BILI_PRODUCT_NAME` 环境变量 |
| `--auto` | AI自动模式（AI生成关键词） | 否 |
| `--dry-run` | 仅测试不发表 | 否 |
| `-m, --max-comments` | 本运行最多评论数 | 从配置文件读取 |
| `-v, --verbose` | 详细输出（-v=DEBUG） | 静默 |

## 评分维度

| 维度 | 权重 | 说明 |
|------|------|------|
| 热度价值 | 25分 | 播放量高、点赞多，曝光量大 |
| 互动潜力 | 25分 | 评论数/播放比高，愿意看评论 |
| 内容契合度 | 25分 | 视频内容与产品目标用户群匹配程度 |
| 评论留人空间 | 25分 | 评论区风格是否适合自然植入软广 |

## 依赖

- Python 3.10+
- bilibili-api-python
- requests
- 千帆 API Key + DeepSeek API Key（`config/llm.json`，兜底 `DEEPSEEK_API_KEY` 环境变量）
- B站登录凭证（扫码获取，自动保存到 `bili_credential.json`）

## 环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key（`config/llm.json` 未配置时兜底） |
| `BILI_PRODUCT_URL` | 默认产品链接 |
| `BILI_PRODUCT_NAME` | 默认产品名称 |
