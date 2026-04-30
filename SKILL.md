---
name: Bili Comment Acquisition
description: |
  B站评论区获客技能
  搜索B站视频 → AI评分 → 生成自然评论 → 自动发表
  基于 bilibili-api-python + DeepSeek API
metadata:
  openclaw:
    emoji: "🎯"
    requires:
      env: ["DEEPSEEK_API_KEY"]
    category: "acquisition"
    tags: ["bilibili", "comment-acquisition", "marketing", "ai"]
---

# B站评论区获客技能

## 流程

```
[1. 关键词] → AI生成 / 指定搜索
      ↓
[2. 搜索] → B站API搜索视频（按播放量排序）
      ↓
[3. 评分] → LLM 4维评分
      ↓
[4. 评论生成] → LLM生成B站风格评论
      ↓
[5. 发表] → B站API发表评论
      ↓
[6. 去重] → JSON持久化
```

## 使用

```bash
# 安装依赖
pip install -r requirements.txt

# 首次扫码登录
python3 scripts/bili_login.py

# 指定关键词运行
python3 scripts/bili_comment_acquisition.py -k "AI工具" -u "https://your-product.com" -n "产品名"

# AI自动模式
python3 scripts/bili_comment_acquisition.py --auto -u "https://your-product.com" -n "产品名"
```

## 依赖

- Python 3.10+
- bilibili-api-python
- DeepSeek API Key（`DEEPSEEK_API_KEY` 环境变量）
- B站登录凭证（扫码获取）
