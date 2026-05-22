---
name: Bilibili Comment Acquisition
description: |
  B站评论区获客技能。搜索B站视频 → AI 4维评分 → LLM生成自然评论 → 自动发表。
  基于 bilibili-api-python + 千帆/DeepSeek API，支持 SPA 产品页面抓取。
metadata:
  openclaw:
    emoji: "📺"
    requires:
      env: ["DEEPSEEK_API_KEY"]
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

## 评分维度

| 维度 | 权重 | 说明 |
|------|------|------|
| 热度价值 | 25分 | 播放量高、点赞多，曝光量大 |
| 互动潜力 | 25分 | 评论数/播放比高，愿意看评论 |
| 内容契合度 | 25分 | 视频内容与产品目标用户群匹配程度 |
| 评论留人空间 | 25分 | 评论区风格是否适合自然植入软广 |

## 评论风格

| 风格 | 说明 |
|------|------|
| 赞同共鸣型 | 对视频内容表示强烈认同 |
| 补充分享型 | 补充自身经验，自然植入产品（含链接） |
| 提问互动型 | 提出开放性问题 |
| 经验交流型 | 分享自身经历 |

## 搜索结果排序策略

每个关键词同时按两种排序搜索并合并去重：
- **click（播放量排序）** — 找到热门爆款视频
- **pubdate（发布时间排序）** — 找到近期新视频（min_play=100）

## 项目结构

```
bili_comment_acquisition/
├── bili_comment_acquisition.py   # 评论区获客主程序
├── bili_llm.py                   # LLM 调用模块（本地独立，无跨技能依赖）
│   ├── call_llm()                # 通用 LLM 调用（千帆主用，DeepSeek 备选）
│   ├── call_llm_json()           # JSON 格式响应解析
│   ├── fetch_webpage_text()      # 网页内容抓取（含 SPA 元信息兜底）
│   └── analyze_product()         # 产品页面分析 → 结构化信息提取
├── bili_acquisition_skill.md     # 技能文档
├── config/
│   ├── publish.json                 # 发布配置
│   ├── keywords.json                # 种子关键词
│   └── llm.json                     # LLM 配置（千帆主用 + DeepSeek 备用）
└── data/                         # 运行数据（已 gitignore）
    ├── bili-commented-history.json  # 评论历史去重
    └── bili_acq_*.log              # 运行日志（自动清理7天前的）
```

## 依赖

- Python 3.10+
- bilibili-api-python
- requests
- 千帆 API Key + DeepSeek API Key（`config/llm.json`）
- B站登录凭证（bili_credential.json，首次需扫码登录）

## 快速使用

```bash
# 手动指定关键词
.venv/bin/python3 bili_comment_acquisition.py -k "AI工具" -u "https://your-product.com"

# 自动模式（AI生成关键词 → 搜索 → 评分 → 评论）
.venv/bin/python3 bili_comment_acquisition.py --auto -u "https://your-product.com" -n "产品名"

# Dry-run 安全测试（不发表）
.venv/bin/python3 bili_comment_acquisition.py -k "数据分析" --dry-run -vv

# 限制评论数（默认从配置文件读取 max_comments_per_run）
.venv/bin/python3 bili_comment_acquisition.py -k "效率工具" -m 3

# 指定多个关键词
.venv/bin/python3 bili_comment_acquisition.py -k AI工具 效率工具 大模型 -u "https://your-product.com"
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-k, --keyword` | 搜索关键词（可指定多个） | 从 seed keywords 随机取 |
| `-u, --product-url` | 产品链接（必填） | 环境变量 `BILI_PRODUCT_URL` |
| `-n, --product-name` | 产品名称 | 环境变量 `BILI_PRODUCT_NAME` |
| `--auto` | AI自动模式（AI生成关键词） | 否 |
| `--dry-run` | 仅测试不发表 | 否 |
| `-m, --max-comments` | 本运行最多评论数 | 从配置文件读取 |
| `-v, --verbose` | 详细输出（-v=DEBUG, -vv=DEBUG+） | 静默 |

## 风控策略

- ✅ 活跃时段评论（默认 8:00-23:00）
- ✅ 每日上限（默认 20）/ 每小时上限（默认 5）
- ✅ 抖动延迟：每次评论间隔随机 48-90 秒
- ✅ 历史去重：永不重复评论
- ✅ 搜索间隔：每次搜索后停顿 0.8-1.5 秒
- ✅ 并发控制：视频详情获取最多 5 个并发
- ✅ Dry-run 安全测试模式
