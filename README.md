# Personal Finance AI — Telegram MVP

这是一个只允许指定 Telegram 用户使用的私人记账机器人。你用自然语言描述一笔支出，DeepSeek 会把它整理成结构化数据；机器人先显示审核卡片，只有你按下“确认”后才会写入本机 SQLite。

当前 MVP 已包括：

- Telegram 私聊自然语言记账。
- 数字 user ID allowlist，在调用 AI 和数据库之前拦截未授权用户。
- DeepSeek `deepseek-flash` JSON 模式与本地 Pydantic 二次验证。
- 金额、币种、日期、商户、分类、账户、备注、信心和歧义字段。
- 确认、重新描述修改、取消流程。
- SQLite 持久化草稿和已确认交易；机器人重启后草稿仍存在。
- `/recent` 查看最近 10 笔已确认记录。

## 1. 准备

需要 Python 3.10 或以上。M1 Pro 16GB 足够运行，因为 AI 推理在 DeepSeek 云端完成。

在 Telegram 找 `@BotFather`，执行 `/newbot` 取得 bot token。到 DeepSeek API 控制台建立 API key。不要把 token 或 key 发到聊天、截图或提交到 Git。

先确认本机 Python 的 HTTPS 支持正常：

```bash
python3 -c "import ssl; print(ssl.OPENSSL_VERSION)"
```

如果这里报错，请先安装一个完整的 Python（例如 `brew install python@3.12`），再继续。

如果还不知道自己的 Telegram 数字 ID：

1. 先在 `.env` 暂时写 `TELEGRAM_ALLOWED_USER_IDS=0`。
2. 启动 bot 后，在 Telegram 对自己的 bot 发送 `/whoami`。
3. 把显示的数字 ID 写回 `.env`，然后重启 bot。

`/whoami` 只会告诉发送者自己的 ID，不会读取财务数据或调用 AI。

## 2. 安装

```bash
cd "/Users/aaron/Documents/Project/Personal AI Finance"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

打开 `.env`，至少替换这三项：

```dotenv
TELEGRAM_BOT_TOKEN=你的_BotFather_token
TELEGRAM_ALLOWED_USER_IDS=你的数字ID
DEEPSEEK_API_KEY=你的_DeepSeek_API_key
```

多个获准用户用逗号分隔，例如 `123456789,987654321`。默认账户标签已包括马来西亚 UOB、Maybank，以及新加坡 Citibank、UOB、DBS、OCBC；可以在 `KNOWN_ACCOUNTS` 修改。默认币种是 SGD，信息里出现 RM 或 Malaysia 时会要求 AI 使用 MYR。

## 3. 运行

```bash
source .venv/bin/activate
finance-bot
```

保持这个 Terminal 窗口运行，然后在 Telegram 发送：

```text
今天在 FairPrice 买菜 S$42.80，用 OCBC 卡
```

看到审核卡片后选择确认、修改或取消。已确认数据保存在 `data/finance.db`。

## 4. 测试

测试不会访问 Telegram 或 DeepSeek，也不需要真实密钥：

```bash
source .venv/bin/activate
pytest
```

## 安全说明

- `.env`、虚拟环境和 SQLite 数据库已列入 `.gitignore`。
- 配置只接受 HTTPS DeepSeek endpoint。
- 未授权用户不会触发 DeepSeek 请求或财务数据读写。
- AI 输出永远先经过严格 schema 验证，再让用户确认。
- 你发送的支出文字会传给 DeepSeek API 进行解析；不要在描述里加入不必要的敏感资料。
- SQLite 数据库是本机明文文件，不是加密保险库；不要把 `.env` 或 `data/finance.db` 同步到公开位置。

## 下一阶段

当前版本刻意只完成可靠的 Telegram 记账闭环。后续可在相同 SQLite 交易结构之上加入月结单上传、银行字段映射、对账匹配、重复检测，以及只读 dashboard。
