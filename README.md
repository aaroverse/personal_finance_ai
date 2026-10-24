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

需要 Python 3.11 或以上（当前本机虚拟环境已准备好）。M1 Pro 16GB 足够运行，因为 AI 推理在 DeepSeek 云端完成。

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

## 5. 打开账本页面

保持 Telegram bot 原来的窗口运行，另外打开一个 Terminal：

```bash
cd "/Users/aaron/Documents/Project/Personal AI Finance"
source .venv/bin/activate
python -m finance_bot.web
```

在当前电脑的浏览器打开 **http://127.0.0.1:8765**。

首次打开页面时，设置至少 12 个字符的本机页面密码（不是银行密码，也不是 Telegram 密码）。密码只保存为哈希；登录后才能读取账本和执行修改。重启网页服务后需要重新登录，登录会话最长保留 12 小时。

页面使用 `.env` 中 `DATABASE_PATH` 指向的同一份 SQLite，按 `TELEGRAM_ALLOWED_USER_IDS` 中的用户隔离数据。单个用户自动选择；如果有多个用户，启动时显式选择自己的 ID。页面密码属于这份本机账本实例；目前不是多用户独立登录系统：

```bash
python -m finance_bot.web --user-id 你的数字ID
```

无需重新填写 token 或 API key；看页面不调用 DeepSeek，不产生 AI 调用费用。默认显示当前月份，点“重置”可看全部月份。筛选后显示对应消费总额；待确认草稿、转账和取现不计入消费总额。SGD 与 MYR 分开统计，不做换汇。

点击记录可以查看原始描述，也可以点“修改记录”修改日期、金额、币种、商户、分类、账户和备注。修改保留原始 Telegram 描述，并将前后内容保存在 `ledger_edits` 修改历史表。

待确认列表里的草稿可以在网页修改、确认入账或取消；保存草稿本身不会入账。审核操作会再显示确认提示。记录已被其他页面或 Telegram 修改／处理时，旧操作会被拒绝，关闭详情刷新后再审核。

首次启动新版网页前，会使用 SQLite 在线备份在 `data/backups/` 留下完整备份，再添加修改历史表。现有 Telegram 账本表保持兼容。不要将备份公开上传。

更新后请重启 Telegram bot（原窗口 Control+C，再运行 `finance-bot`），启用带记录版本的审核按钮。旧版审核卡片在新版 bot 中会提示到网页查看最新草稿，避免按旧内容确认；历史草稿可在网页继续审核。不要同时运行两份 Telegram bot。

页面在前台时每 5 秒自动更新，不调用 AI。正在编辑筛选、查看详情或请求尚未完成时暂停，防止打断操作；手动刷新仍可使用。

默认只允许本机访问。私人远程访问使用下述 Tailscale Serve 配置；不要直接把此本机服务端口暴露到公网。

密码哈希位于数据库旁的 `finance.db.web-auth.json`（默认路径）。请保留该文件；它不含明文密码，且不会提交 Git。忘记密码时需在本机验证并处理重设，不提供通过网页绕过密码的入口。

端口占用时可使用 `python -m finance_bot.web --port 8767`。

## 6. 手机私人访问（Tailscale）

MBP 和手机需要安装 Tailscale 并登录同一账号。本机先设置好账本密码。

在数据库旁建立 `finance.db.remote.json`（默认 `data/finance.db.remote.json`，已被 Git 忽略）：

```json
{
  "origin": "https://你的设备.你的网络.ts.net",
  "tailscale_login": "你的Tailscale登录账号"
}
```

填入 Tailscale 实际分配的完整地址和登录账号，不是示例文字。也可以通过 `WEB_PUBLIC_ORIGIN` 和 `WEB_TAILSCALE_LOGIN` 环境变量覆盖。

重启账本服务后，配置只在私人网络内使用的 HTTPS 入口：

```bash
tailscale serve --bg http://127.0.0.1:8765
tailscale serve status
```

如果显示 HTTPS 启用链接，打开后启用 Serve／HTTPS；不启用 Funnel（公网入口）。证书申请会将设备 DNS 名发布到证书透明度日志，账本数据不会因此公开。

手机打开 Tailscale，访问 Serve 显示的 HTTPS 网址，用页面密码登录。账本会同时检查地址、Tailscale 账号和页面登录状态。请求经本机代理转发，Python 仍只监听 127.0.0.1。不要改为监听 0.0.0.0。

手机关闭 Wi-Fi 后使用流量测试。MBP 需要持续唤醒、联网，Tailscale 和账本服务也需要运行；Serve 的后台设置不会自动启动 Python。停止这个入口可使用 `tailscale serve --https=443 off`，不会删除账目。

## 产品计划

完整更新计划见 [docs/PLAN.md](docs/PLAN.md)。已确定不做导出；statement 从页面上传，逐步加入信用卡对账和 Tailscale 私人远程访问。

## 下一阶段

当前已有 Telegram 记账闭环、带登录的本机账本、网页修改和审核。接下来配置私人远程访问，再逐步加入月结单上传及对账。
# UOB 新加坡信用卡账单预览

登录后点击账本顶部的「上传账单」，选择原始电子 PDF，再点击「识别并预览」。不必选择银行：系统按文件内容识别支持的格式，成功后才显示银行及卡片。账本不再为每家银行设置单独区块；关闭弹窗会保留本次核对选择，重新打开可继续。支持范围及隐私说明收在上传窗口中。

目前仅支持已验证的新加坡 UOB 信用卡英文 SGD 结算格式、多张卡及跨页明细；不表示已支持其他银行。识别不出的格式会明确拒绝，不依赖文件名或套用 UOB 解析器。扫描图片、密码保护 PDF、银行存款流水暂不支持。文件最多 6 MB、20 页、1000 笔，单次解析最多 20 秒，同时最多两个解析任务。

PDF 通过现有私人页面传到 MBP，仅在本机内存及独立子进程解析，不发给 DeepSeek、不保存 PDF。未确认预览与登录会话绑定，30 分钟后过期，重启服务后需重新上传；刷新、退出或清除预览会清除当前页面结果。仅在逐笔确认时才把银行明细及来源校验值写入本机数据库，不保存姓名、地址、完整卡号或交易参考号。已确认记录不会被「清除预览」删除。

逐卡显示上期余额、本期净变动与期末余额校验。CR 还款不作为消费候选，其他 CR 标记为贷记／退款待后续核对；外币行保留原币金额，但使用 SGD 结算金额寻找候选。余额通过不代表每行已经人工核对。

候选必须金额、币种一致且消费日期相差不超过 3 天；排除已注明其他银行、马来西亚或不同卡尾号的记录。账户缺失或只写 UOB 时会提示核对具体卡片。仅查当前账本主人的已确认消费，不比较草稿。候选只展示前五笔，可能被多行推荐，**不代表已经匹配**。

每笔消费可选择「匹配已有记录」或「补录为新记录」，核对分类与「计入我的个人支出」后，点击「核对并确认」，再检查摘要并保存。分类由用户选择，不调用 AI；匹配时沿用原记录的分类和归属作为初始值。妈咪刷的取消勾选即可：记录、卡片及对账关联仍完整保留，但不计入个人金额和分类汇总。交易详情也可以改回，旧记录默认个人支出；这不是欠款或分账功能。

匹配不会新增一笔消费，保留 Telegram 原描述和银行商户两份来源。每笔记账最多关联一条账单明细；原始 PDF 校验值与稳定的逐行指纹（包含相同行的出现次数）防止重复处理、同时双击及相同内容 PDF 再上传。银行重新改写交易描述／日期的变体仍需人工核对，不保证跨所有重发格式自动去重。有可能重复时，补录需额外勾选这是另一笔消费；不会默认自动匹配。

已关联记录的金额、币种、消费日期和账户锁定以防破坏对账；分类、个人归属、备注与用户描述仍可编辑并记录历史。还款、退款／其他贷记、余额校验失败行不支持补录；OCR、解析字段修正、撤销关联、部分分摊留待后续。首次创建关联表前自动备份本地数据库。原 PDF 不进入 Git；测试只用合成数据。
