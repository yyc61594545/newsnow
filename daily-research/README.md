# 每日多平台调研系统（daily-research）

用 **Claude Code + GitHub Actions** 实现的自动化调研：一条任务同时调研
**Reddit / YouTube / Hacker News / Polymarket**（X/Twitter 暂跳过），按真实互动热度
排序、过滤低质与重复，用 LLM 综合成**中文 markdown 简报**，再通过 **Telegram** 推送，
并把简报存档到 `briefs/`。

> 鉴权只用订阅 OAuth（`CLAUDE_CODE_OAUTH_TOKEN`），**绝不使用 `ANTHROPIC_API_KEY`**
> （会按 API 费率计费）。工作流第一步即守卫：检测到 `ANTHROPIC_API_KEY` 直接失败。

## 目录结构
```
daily-research/
  CLAUDE.md              # 5 个任务的主题/要求/格式（执行权威说明）
  README.md             # 本文件
  briefs/<任务ID>/<日期>.md   # 简报存档
  scripts/
    aggregate.py        # 回退用的多平台聚合脚本（无需任何 API key）
    install_skill.sh    # 在 runner 上安装 last30days skill（best-effort）
.github/workflows/
  research.yml          # 可复用引擎（被下面 5 个调用）
  research-ai-tools.yml         # 「AI工具与变现」每天 08:00
  research-money.yml            # 「搞钱与撸毛」每天 08:05
  research-uscards.yml          # 「美卡」每隔一天 08:10
  research-self-improve.yml     # 「自我提升」每周一 09:00
  research-foreign-trade.yml    # 「外贸获客」每周一 09:10
```
定时均为 **Asia/Shanghai (UTC+8)**；当前所有定时已注释，仅保留手动触发
（`workflow_dispatch`）。

## 调研引擎
- **优先**使用 [last30days skill](https://github.com/mvanhorn/last30days-skill)
  （Reddit / Hacker News / Polymarket 零配置免 key，YouTube 用 runner 上安装的 `yt-dlp`）。
- **回退**：若 skill 不可用，自动改用 `scripts/aggregate.py`（同样免 key）。

## 一次性配置（需要你做）
在仓库 **Settings → Secrets and variables → Actions** 添加 3 个 secret：

| Secret | 用途 | 获取方式 |
| --- | --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | 订阅鉴权（计费走订阅，不走 API） | 本机 `claude setup-token`（需 Claude Pro/Max） |
| `TG_BOT_TOKEN` | Telegram 机器人 token | 找 [@BotFather](https://t.me/BotFather) 建 bot |
| `TG_CHAT_ID` | 你的会话 ID | 找 [@userinfobot](https://t.me/userinfobot)，或先给 bot 发消息后调 `getUpdates` |

> **不要**添加 `ANTHROPIC_API_KEY`——加了工作流会主动失败。

## 先测后跑
1. 把本分支合并到 `main`（合并后 Actions 页才会出现「Run workflow」按钮）。
2. 添加上面 3 个 secret。
3. Actions → **研究·AI工具与变现** → **Run workflow** 手动跑一遍。
4. 检查：Telegram 是否收到简报、内容质量、`briefs/ai-tools/<日期>.md` 是否生成。
5. **核对计费走订阅而非 API**：到 Anthropic Console 看 API 用量——本次运行应为 **$0**；
   订阅用量里应能看到这次消耗。工作流日志里 “Guard auth” 步骤会确认未使用 API key。

## 验证通过后启用定时
逐个编辑 `.github/workflows/research-*.yml`，取消 `schedule:` 段的注释即可。
时间已错开，避免同时触发。

## 简报格式
标题 + 平台/热度指标 + 2–3 句中文摘要 + 原文链接（详见 `CLAUDE.md`），每篇最多 12 条。

## 手动跑某个任务（命令行，可选）
```
gh workflow run research-ai-tools.yml --ref main
```
