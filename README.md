# B站评论区获客工具 🎯

> 通过 B站 API 自动化评论区获客：搜索视频 → AI 评分 → 生成自然评论 → 自动发表

## 功能

| 步骤 | 方式 | 说明 |
|------|------|------|
| 🔍 搜索视频 | B站 API | 双排序搜索（播放量+发布时间），合并去重 |
| 🤖 AI 评分 | DeepSeek | 4维评分（热度+互动+契合度+留人空间） |
| 📝 生成评论 | DeepSeek | 4种风格可选，自然口语化，强制植入产品名+官网链接 |
| 💬 发表评论 | B站 API | 自动发表至目标视频评论区 |
| 📄 历史去重 | JSON | 持久化记录，永不重复评论 |
| 🌐 产品分析 | 真实抓取 | SPA 页面也可读取（meta 标签兜底） |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

需要 Python 3.10+。

### 2. 安装 B站依赖

```bash
pip install bilibili-api-python requests
```

### 3. B站扫码登录

```bash
python3 bili_login.py
```

终端会显示二维码，用B站App扫码即可（凭证自动保存到 `bili_credential.json`）。

### 4. 配置 LLM

编辑 `config/llm.json`，填入你的 API Key（支持千帆主用 + DeepSeek 备用）：

```json
{
  "primary": {
    "api_key": "你的千帆API Key"
  },
  "fallback": {
    "api_key": "你的DeepSeek API Key"
  }
}
```

也可以设置环境变量 `DEEPSEEK_API_KEY` 作为兜底。

### 5. 运行

```bash
# 手动指定关键词
python3 bili_comment_acquisition.py -k "AI工具" -u "https://your-product.com" -n "产品名"

# AI自动模式（自动生成关键词 → 搜索 → 评分 → 评论）
python3 bili_comment_acquisition.py --auto -u "https://your-product.com" -n "产品名"

# Dry-run 安全测试（只搜索评分，不发表）
python3 bili_comment_acquisition.py -k "数据分析" --dry-run -vv

# 限制评论数量
python3 bili_comment_acquisition.py -k "效率工具" -m 3 --dry-run

# 多个关键词
python3 bili_comment_acquisition.py -k AI工具 效率工具 大模型 -u "https://your-product.com"
```

## 评论风格

| 风格 | 说明 | 适用场景 |
|------|------|----------|
| 赞同共鸣型 | 对内容表示强烈认同 | 干货教程、观点类 |
| 补充分享型 | 补充自身经验，自然植入产品+链接 | 经验分享、评测类 |
| 提问互动型 | 提出开放性问题 | 教程、科普类 |
| 经验交流型 | 分享自身经历，含链接 | 踩坑、对比类 |

## 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-k, --keyword` | 搜索关键词（可指定多个） | 从 `keywords.json` 随机取 |
| `-u, --product-url` | 产品链接（必填） | 环境变量 `BILI_PRODUCT_URL` |
| `-n, --product-name` | 产品名称 | 环境变量 `BILI_PRODUCT_NAME` |
| `--auto` | AI自动模式（AI生成关键词） | 否 |
| `--dry-run` | 仅测试不发表 | 否 |
| `-m, --max-comments` | 本次运行最多评论数 | 从配置读取 |
| `-v, --verbose` | 详细输出 | 静默 |

## 工作流程

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

## 配置

`config/publish.json`：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_comments_per_run` | 5 | 每次运行最多评论 |
| `max_comments_per_day` | 20 | 每天评论上限 |
| `max_comments_per_hour` | 5 | 每小时评论上限 |
| `base_interval_seconds` | 60 | 评论间隔（秒） |
| `active_hours` | [8, 23] | 活跃时段 |
| `search.min_play` | 100 | 最低播放量过滤 |
| `search.min_review` | 2 | 最低评论数过滤 |
| `search.max_comment_count` | 500 | 视频最大评论数过滤 |
| `search.result_count` | 20 | 每轮搜索取前N个 |

### config/keywords.json

AI 自动模式下使用的种子关键词：

```json
["AI工具", "效率工具", "数据分析", "Python教程", "AI编程", "大模型", "自动化办公", "程序员效率"]
```

## 目录结构

```
bili-comment-acquisition/
├── README.md
├── bili_acquisition_skill.md        # OpenClaw 技能文档
├── requirements.txt                 # Python 依赖
├── bili_comment_acquisition.py      # 主程序（async/await）
├── bili_llm.py                      # LLM 调用模块（独立，含SPA网页抓取）
├── bili_login.py                    # 命令行扫码登录
├── scripts/
│   ├── bili_login.py                # 备用扫码登录
│   └── xhs_llm.py                   # 兼容脚本
├── config/
│   ├── publish.json                 # 发布配置
│   ├── keywords.json                # 种子关键词
│   └── llm.json                     # LLM 配置（千帆主用 + DeepSeek 备用）
├── data/                            # 运行时数据（自动创建，已gitignore）
│   ├── bili-commented-history.json  # 评论历史去重
│   └── bili_acq_*.log               # 运行日志（自动清理7天前的）
└── bili_credential.json             # 登录凭证（扫码后自动生成，已gitignore）
```

## 风控策略

- ✅ 活跃时段评论（默认 8:00-23:00）
- ✅ 每日上限（默认 20）/ 每小时上限（默认 5）
- ✅ 抖动延迟：每次评论间隔随机 48-90 秒
- ✅ 历史去重：永不重复评论
- ✅ 搜索间隔：每次搜索后停顿 0.8-1.5 秒
- ✅ 并发控制：视频详情获取最多 5 个并发
- ✅ Dry-run 安全测试模式

## 注意事项

- ⚠️ B站新账号（未转正）不能发表评论，请使用已转正的老号
- ⏰ 默认在 8:00-23:00 活跃时段运行
- 🛡️ 内置抖动延迟 + 每日/小时限制 + 历史去重
- 🔍 建议先用 `--dry-run` 测试

## 技术栈

- [bilibili-api-python](https://github.com/Nemo2011/bilibili-api) — B站 API 封装
- DeepSeek — LLM 评分与评论生成
