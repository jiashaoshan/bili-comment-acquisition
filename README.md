# B站评论区获客工具 🎯

> 通过 B站 API 自动化评论区获客：搜索视频 → AI 评分 → 生成自然评论 → 自动发表

## 功能

| 步骤 | 方式 | 说明 |
|------|------|------|
| 🔍 搜索视频 | B站 API | 按关键词搜索，支持播放量/最新排序 |
| 🤖 AI 评分 | DeepSeek | 4维评分（热度+互动+契合度+留人空间） |
| 📝 生成评论 | DeepSeek | 4种风格可选，自然口语化，带产品植入 |
| 💬 发表评论 | B站 API | 自动发表至目标视频评论区 |
| 📄 历史去重 | JSON | 持久化记录，永不重复评论 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. B站扫码登录

```bash
python3 scripts/bili_login.py
```

终端会显示二维码，用B站App扫码即可（凭证自动保存到 `bili_credential.json`）。

### 3. 配置环境变量

```bash
export DEEPSEEK_API_KEY="你的DeepSeek Key"
```

### 4. 运行

```bash
# 指定关键词（推荐）
python3 scripts/bili_comment_acquisition.py -k "AI工具" -u "https://your-product.com" -n "产品名"

# AI自动模式（自动生成关键词）
python3 scripts/bili_comment_acquisition.py --auto -u "https://your-product.com" -n "产品名"

# Dry-run 安全测试
python3 scripts/bili_comment_acquisition.py -k "数据分析" --dry-run -vv

# 限制评论数量
python3 scripts/bili_comment_acquisition.py -k "效率工具" -m 3 --dry-run
```

## 评论风格

| 风格 | 说明 | 适用场景 |
|------|------|----------|
| 赞同共鸣型 | 对内容表示强烈认同 | 干货教程、观点类 |
| 补充分享型 | 补充自身经验，自然植入产品 | 经验分享、评测类 |
| 提问互动型 | 提出开放性问题 | 教程、科普类 |
| 经验交流型 | 分享自身经历 | 踩坑、对比类 |

## 配置

`config/publish.json`：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_comments_per_run` | 5 | 每次运行最多评论 |
| `max_comments_per_day` | 20 | 每天评论上限 |
| `max_comments_per_hour` | 3 | 每小时评论上限（代码 DEFAULT_CONFIG 中的备选默认值为 5，实际使用 `publish.json`） |
| `base_interval_seconds` | 60 | 评论间隔（秒） |
| `active_hours` | [8, 23] | 活跃时段 |
| `search.min_play` | 1000 | 最低播放量过滤 |
| `search.min_like_ratio` | 0.01 | 最低点赞/播放比 |
| `search.max_comment_count` | 500 | 视频最大评论数过滤 |
| `search.result_count` | 20 | 每轮搜索取前N个 |
| `scoring.hot_weight` | 0.35 | 热度评分权重 |
| `scoring.interact_weight` | 0.35 | 互动评分权重 |
| `scoring.fresh_weight` | 0.20 | 时效评分权重 |
| `scoring.quality_weight` | 0.10 | 质量评分权重 |

### config/keywords.json

AI 自动模式下使用的种子关键词：

```json
["AI工具", "效率工具", "数据分析", "Python教程", "AI编程", "大模型", "自动化办公", "程序员效率"]
```

## 目录结构

```
bili-comment-acquisition/
├── README.md
├── SKILL.md
├── requirements.txt
├── scripts/
│   ├── bili_comment_acquisition.py  ← 主入口
│   ├── bili_login.py               ← 扫码登录
│   └── xhs_llm.py                  ← LLM 调用封装
├── config/
│   ├── publish.json                 ← 发布配置
│   └── keywords.json                ← 种子关键词（AI自动模式使用）
├── data/                            ← 运行时数据（自动创建）
│   └── bili-commented-history.json  ← 评论历史去重
└── bili_credential.json             ← 登录凭证（扫码后自动生成于此）
```

## 注意事项

- ⚠️ B站新账号（未转正）不能发表评论，请使用已转正的老号
- ⏰ 默认在 8:00-23:00 活跃时段运行
- 🛡️ 内置抖动延迟 + 每日/小时限制 + 历史去重
- 🔍 建议先用 `--dry-run` 测试

## 技术栈

- [bilibili-api-python](https://github.com/Nemo2011/bilibili-api) — B站 API 封装
- DeepSeek V4 — LLM 评分与评论生成
