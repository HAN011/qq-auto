# QQ 群消息智能管家

基于 NapCat（OneBot v11）+ Python + DeepSeek API 的 QQ 群消息助手，支持消息入库、历史检索、智能查询、定时摘要、重要消息提醒和跨群汇总。

当前仓库已整理为可在 Ubuntu 上运行，功能逻辑与原 Windows 版本保持一致。Python 业务代码本身不依赖 Windows，迁移重点在于 NapCat 安装方式和启动脚本。

## 1. 环境准备

- Ubuntu 20.04+（建议 22.04 / 24.04）
- Python 3.10+
- Node.js 18+（仅当你需要用 `loadNapCat.js` 加载 NapCat 时）
- 已登录并可正常工作的 NapCat
- 已开启 OneBot v11 正向 WebSocket
- 可用的 DeepSeek API Key

建议先创建虚拟环境：

```bash
cd /path/to/qq-auto
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## 2. 安装 NapCat（Ubuntu）

本项目通过 OneBot v11 WebSocket 与 NapCat 通信，因此 Ubuntu 上最关键的是先把 NapCat 本体跑起来。

可选方式：

1. 按 NapCat 官方 Linux 安装方式安装，并确认最终存在一个 `napcat.mjs`
2. 如果你已经手工安装，只要能拿到 `napcat.mjs` 的实际路径即可

仓库内提供了跨平台加载器 `loadNapCat.js`，它会按以下顺序查找 NapCat 入口：

- 环境变量 `NAPCAT_MJS_PATH`
- 环境变量 `NAPCAT_HOME/napcat.mjs`
- 仓库当前目录下的 `napcat.mjs`
- Ubuntu 常见路径：
  `/opt/QQ/resources/app/app_launcher/napcat.mjs`
  `/opt/napcat/napcat.mjs`
  `/usr/local/lib/napcat/napcat.mjs`
  `~/.local/share/NapCat/napcat.mjs`
  `~/.napcat/napcat.mjs`

如果 NapCat 安装位置不在这些路径，显式指定即可：

```bash
export NAPCAT_MJS_PATH=/absolute/path/to/napcat.mjs
node loadNapCat.js
```

## 3. 配置说明

复制模板并填写实际参数：

```bash
cp .env.example .env
```

主要配置项：

- `NAPCAT_WS_URL`：NapCat 的 OneBot v11 WebSocket 地址
- `NAPCAT_ACCESS_TOKEN`：NapCat 鉴权 token
- `DEEPSEEK_API_KEY`：DeepSeek API Key
- `DEEPSEEK_BASE_URL`：DeepSeek 接口根地址，默认 `https://api.deepseek.com`
- `DEEPSEEK_MODEL`：模型名，默认 `deepseek-chat`
- `BOT_QQ`：机器人自身 QQ，用于忽略自己的消息
- `WATCH_GROUPS`：监听群号，逗号分隔；填 `all` 表示监听当前账号所在全部群
- `HISTORY_SYNC_ENABLED`：启动时是否自动回补最近历史消息，默认 `true`
- `HISTORY_SYNC_COUNT`：每个群启动时最多回补多少条最近历史消息，默认 `1000`
- `SUMMARY_TARGET_QQ`：接收提醒和摘要的 QQ
- `SUMMARY_CRON`：定时摘要 cron 表达式
- `SUMMARY_LOOKBACK_HOURS`：定时摘要默认回看小时数，默认 `24`
- `LOG_LEVEL`：日志级别，默认 `INFO`

默认文件位置：

- 数据库：`data/bot.db`
- 日志：`logs/bot.log`

## 4. 启动

先确保 NapCat 已启动，并且 OneBot v11 正向 WebSocket 可以连通。

如果你需要用仓库里的加载器启动 NapCat：

```bash
node loadNapCat.js
```

再启动 Python 机器人：

```bash
source .venv/bin/activate
python3 main.py
```

启动后会：

- 打印脱敏后的配置摘要
- 连接 NapCat WebSocket
- 自动回补最近一批群历史消息
- 开始实时监听并写入 SQLite

## 5. 功能使用

### 5.1 消息持久化

- 监听命中的群消息会自动写入 SQLite
- 当 `WATCH_GROUPS=all` 时，会监听当前账号所在全部群
- 启动时会调用 NapCat 的 `get_group_msg_history` 自动回补最近历史消息
- 如果机器人运行过程中断线，重连成功后会自动补录断线开始到重连完成之间的最近一批历史消息
- 已按 `message_id + group_id` 去重，重复重启不会重复入库

说明：

- 历史回补依赖 NapCat 当前还能返回的最近消息
- 断线补录同样依赖 NapCat 历史接口的可返回范围；群消息特别密集时，可适当调大 `HISTORY_SYNC_COUNT`
- 它适合补最近一段，不等于完整永久聊天归档

### 5.2 关键词搜索

群聊或私聊发送：

```text
搜索 课程安排
搜索 作业 123456789
搜索 报名 最近3天
搜索 竞赛通知 机器人协会群
```

说明：

- 不指定群时，默认搜索全部已入库群消息
- 指定群号或群名时，只搜索该群
- 支持 `最近N天` 和 `最近N小时`
- 返回最近 20 条匹配消息

### 5.3 智能查询

群聊或私聊发送：

```text
查询 最近3天谁发过报名链接
查找 保研群 复试时间
帮我找 最近6小时有哪些通知
帮我查 奖学金 相关消息
看看 最近1天谁提到面试
```

说明：

- 这是比 `搜索` 更自然的入口，适合按问题来找内容
- 群里触发时，默认只查当前群
- 私聊触发时，默认查全部已监听群
- 会优先整理链接、文件、通知、截止时间、发言人和关键结论
- 如果现有记录不足，机器人会明确提示无法确认

### 5.4 手动摘要

群聊中发送：

```text
总结
总结 最近6小时
```

私聊中发送：

```text
总结
总结 最近12小时
总结 保研 最近24小时
总结 夏令营 最近3小时
```

说明：

- 群聊里默认总结当前群
- 私聊里默认总结全部监听群
- 支持 `总结 主题 最近N小时` 的主题摘要，例如保研、夏令营、简历、机试
- 结果会回复当前会话，并同步私聊推送给 `SUMMARY_TARGET_QQ`

### 5.5 定时摘要

- 根据 `SUMMARY_CRON` 自动执行
- 默认汇总最近 `SUMMARY_LOOKBACK_HOURS` 小时内容
- 摘要会私聊发送给 `SUMMARY_TARGET_QQ`

### 5.6 自动监控与提醒

监听群中出现以下内容时，会自动私聊提醒主号：

- 含 URL 或链接
- 含 `通知 / 公告 / 截止 / 报名 / 提交`
- `@全体成员`
- 文件分享

说明：

- 纯图片、纯表情包默认不提醒，避免误报
- 如果图片同时带有关键字、链接或 `@全体成员`，仍会提醒

提醒格式示例：

```text
⚠️ [某某群] 发现重要消息
发送人：张三
内容：请尽快提交报名表 https://example.com
时间：2026-04-19 21:30:00
```

### 5.7 跨群汇总

私聊发送：

```text
汇总 运动会
汇总 项目答辩
```

机器人会在全部已入库消息里搜索相关内容，用 AI 去重整合后返回跨群讨论要点。

## 6. 项目结构

```text
qq-auto/
├── main.py
├── config.py
├── db.py
├── napcat_client.py
├── ai_client.py
├── history_sync.py
├── scheduler.py
├── handlers/
│   ├── message_handler.py
│   ├── search_handler.py
│   ├── smart_query_handler.py
│   ├── summary_handler.py
│   ├── monitor_handler.py
│   └── cross_group_handler.py
├── loadNapCat.js
├── .env.example
├── requirements.txt
└── README.md
```

## 7. Ubuntu 迁移说明

这次迁移保留了原有功能，主要变更只有两类：

- 去掉了 `loadNapCat.js` 中写死的 Windows 路径 `D:/tool/qq-auto/napcat.mjs`
- 将文档中的 PowerShell 命令改为 Ubuntu 可直接执行的 Bash 命令，并补充了 NapCat 路径配置方式

Python 主程序仍然使用原有实现：

- SQLite 异步入库
- NapCat WebSocket 自动重连、心跳、历史回补
- 搜索、智能查询、摘要、提醒、跨群汇总

## 8. 常见问题

### 8.1 `node loadNapCat.js` 提示找不到 `napcat.mjs`

说明 NapCat 不在默认搜索路径中。直接设置：

```bash
export NAPCAT_MJS_PATH=/absolute/path/to/napcat.mjs
node loadNapCat.js
```

### 8.2 `python3 main.py` 能启动，但连不上 WebSocket

优先检查：

- NapCat 是否真的已启动
- `NAPCAT_WS_URL` 是否与 NapCat OneBot v11 正向 WebSocket 监听地址一致
- `NAPCAT_ACCESS_TOKEN` 是否和 NapCat 中配置一致
- Ubuntu 防火墙或端口映射是否阻断本地连接

### 8.3 Python 3.13 是否能用

仓库声明最低要求是 Python 3.10+。如果你在 Ubuntu 上遇到依赖兼容问题，优先切到 Python 3.11 或 3.12。
