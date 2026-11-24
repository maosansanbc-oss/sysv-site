# SysV Radar V2.2 — 关键词竞争调研、独立源码审计与线上技术 SEO 实测报告

> **证据分级声明**：本报告严格区分 **实测事实（Live Test / Code Fact）**、**社区案例（Community Case）**、**推断（Inference）** 与 **商业假设（Commercial Hypothesis）**。未通过 Ahrefs / Semrush / GSC 实测的搜索量均如实标注 `UNKNOWN`。

---

## 一、10 个核心英文关键词与竞争缺口研究（2026-11-24）

| # | 搜索词 | 搜索意图 | 搜索量 | 需求证据 | 现有竞争网站 | 竞争结果质量 | 现有页面是否满足 | 可提供的独有价值 | 自动化数据获取成本 | 商业转化路径 | 建议行动 | 信息来源及日期 |
| :-: | :--- | :--- | :---: | :--- | :--- | :---: | :---: | :--- | :---: | :--- | :--- | :--- |
| 1 | `OpenRouter pricing calculator` | 算清 OpenRouter 真实开销（含 5.5% 充值费、BYOK 门槛与 `:batch` 5折） | UNKNOWN | OpenRouter 生态庞大但无自带任务计算器；第三方工具大量涌现 | CostGoat, APIpulse, aiplans.dev, CostPerPrompt | 中 | 部分满足（多数忽略 5.5% 信用卡费与 `-1` 路由陷阱） | 实时 OpenRouter 470 端点 + 5.5% 卡费/5% 加密货币费 + `:batch` 对比 + `-1` 路由隔离 | ¥0（OpenRouter Public API） | AdSense + AI Gateway / 云主机现金联盟 | **首批上线独立工具页** `/tools/openrouter-cost-calculator/` | OpenRouter Docs / BetterClaw / CostGoat (2026-11-24) |
| 2 | `OpenRouter free model limits` | 查清 `:free` 到底每天能调多少次、是否要充 $10 | UNKNOWN | Reddit/社区高频提问 429 与 402 错误 | OpenRouter Docs, TrueFoundry, Klymentiev 博客 | 中高 | 基本满足，但缺少实时可用 `:free` 列表与能力（Tools/JSON）同屏核对 | 官方限流规则（20 RPM / 未充 50 RPD / 充 $10 升 1000 RPD）+ 每日实测 `:free` 列表与 `tools` 标记 | ¥0（OpenRouter API + 文档核验） | AdSense + 付费 API/网关升级引导 | 强化 `/free-llm-apis/` 专题页 | OpenRouter Docs / Reddit (2026-11-24) |
| 3 | `Free LLM API for n8n` | 在 n8n 工作流里用免费/极低价模型跑自动化 | UNKNOWN | Reddit r/n8n 与 GitHub 高频讨论避开 OpenAI 高额账单 | Reddit r/n8n, 博客文章, n8n 社区帖 | 中低 | 分散在帖子中，常推已过时或不支持 Tool Calling 的模型 | 仅筛选 **支持 Tool Calling + 实时非 Batch** 的免费/低价 OpenAI 兼容端点，附 n8n 节点配置口径 | ¥0（OpenRouter `supported_parameters`） | **高转化**：自托管 n8n VPS 现金联盟（DigitalOcean CJ / Vultr / Railway） | 纳入 `/tools/n8n-ai-workflow-cost-calculator/` 与 `/free-llm-apis/` | Reddit r/n8n / GitHub (2026-11-24) |
| 4 | `n8n AI workflow cost calculator` | 预估 n8n AI 节点月费，防止死循环/重试烧穿账单 | UNKNOWN | GitHub 社区出现 `n8n-cost-calculator`、`n8meter`、`MarginKit` 等开源项目 | GitHub (`enzoemir1`), 零散博文 | 中 | 现有工具缺少每日实时同步的模型价目与 Cloud vs Self-Hosted VPS 总账对比 | 合并 **n8n 执行平台费（Cloud vs $5–$12 VPS）+ 每日实时 LLM Token 费 + 重试倍率系数** | ¥0（OpenRouter API + 公开 VPS/Cloud 公开定价） | **极高现金联盟潜力**（DigitalOcean CJ / Vultr / Railway）+ AdSense | **首批上线独立工具页** `/tools/n8n-ai-workflow-cost-calculator/` | Reddit r/n8n / GitHub (2026-11-24) |
| 5 | `Self-hosted vs cloud n8n cost` | 决策该买 n8n Cloud 还是买 VPS 用 Docker 自托管 | UNKNOWN | Indie Hackers / r/n8n 高频对比帖（Cloud 按执行量涨价痛点明显） | 托管测评站、n8n 社区、Reddit | 中 | 多数为定性文章，缺乏按月执行次数算出的交互式盈亏平衡点 | 同一工具内直接按“月执行数 × 每执行 AI Token 数”算出 Cloud vs VPS 月总成本 | 极低（定期核验公开套餐价） | VPS / PaaS 现金联盟（DigitalOcean CJ / Vultr / Railway） | 合并进 `/tools/n8n-ai-workflow-cost-calculator/`（避免同质拆分） | Reddit / Indie Hackers (2026-11-24) |
| 6 | `LLM cost per 1000 documents` | 估算批量处理/翻译 1,000 份文档的真实 Token 账单 | UNKNOWN | 企业/独立开发者做 RAG、批量摘要和翻译前的标准询价动作 | 厂商博客（Novita / IntuitionLabs）、零散公式帖 | 中低 | 缺少实时同步全网模型、自动把“文档数 × 词数”换算 Token 并支持 `:batch` 5 折的计算器 | 实测公式（1,000 words ≈ 1,333 tokens）+ 实时比价 + 同步 vs `:batch` 5 折一键切换 | ¥0（OpenRouter 含 92 个 `:batch` 端点） | AdSense + 开发者工具联盟 | **首批上线独立工具页** `/tools/llm-translation-cost-calculator/` | Web Research (2026-11-24) |
| 7 | `OpenRouter batch vs realtime cost` | 确认异步 `:batch` 相比实时 API 到底省多少、有哪些限制 | UNKNOWN | OpenRouter 新增 92 个 `:batch` 端点，且自带 24h 异步与 30 天清除规则 | OpenRouter 官方文档 | 中 | 官方文档无按自定义工作量一键横向算出差价的交互工具 | 每日从 OpenRouter 自动配对同模型的 Sync 与 `:batch` 价格，算清差价与异步限制 | ¥0（OpenRouter API） | AdSense | 深度整合进 `/tools/openrouter-cost-calculator/` | OpenRouter Docs (2026-11-24) |
| 8 | `API vs local LLM break even` | 算清买 RTX 4090/5090 或租 GPU 跑本地模型，多久能回本 | UNKNOWN | 开发者社区（WiredWhite、LocalLLaMA、FlavioCopes）长期热点 | WiredWhite 计算器、博客、Reddit | 中高 | 多数文章只有静态表格，未联动每日下降的最新 API 价格 | 硬件购置/折旧月数 + 电费 + 每日最新廉价 API 对比，算清真实回本月数 | ¥0（OpenRouter 实时 API + 硬件参数输入） | AdSense + GPU/云主机联盟 | **首批上线独立工具页** `/tools/api-vs-local-llm-calculator/` | WiredWhite / LocalLLaMA / Web (2026-11-24) |
| 9 | `Free LLM API with tool calling` | 找免费且**真支持 Function/Tool Calling** 的 API 跑 Agent | UNKNOWN | LangGraph / Agent 开发者高频需求 | KDnuggets、Wotai、OpenRouter 文档 | 中 | 多数合集不区分模型是否真正支持 `tools` 参数 | 直接读取 OpenRouter `supported_parameters` 中 `tools=True` 的活跃 `:free` 模型，零猜测 | ¥0（OpenRouter API） | AdSense + 开发者基础设施联盟 | 在 `/free-llm-apis/` 增加 Tool Calling 一键筛选并优化 Title | KDnuggets / OpenRouter (2026-11-24) |
| 10 | `Cheapest LLM for bulk translation` | 百万字/多语言批量翻译找最便宜 API | UNKNOWN | 跨境内容、文档本地化刚需 | Novita AI 软文、IntuitionLabs | 中 | 厂商自卖自夸，缺中立的“字数→Token→总价（含 `:batch`）”实时排行榜 | 合并入 `/tools/llm-translation-cost-calculator/`，默认展示百万字与千文档两套场景 | ¥0（OpenRouter API） | AdSense + 现金联盟 | 合并进 `/tools/llm-translation-cost-calculator/` | Novita / IntuitionLabs (2026-11-24) |

---

## 二、2026-11-24 线上实测技术 SEO 缺陷（黑盒事实）

通过模拟 `Googlebot/2.1` 对 `https://sysv.cn` 进行不跟随跳转（No-Redirect）实测，发现以下 **客观事实**：

1. **HTTPS 强制跳转与 www 协议降级缺陷（P0/P2 实测事实）**：
   * `http://sysv.cn/` 返回 **HTTP 200**（未 301 跳转至 `https://sysv.cn/`）。
   * `https://www.sysv.cn/` 返回 **HTTP 301 → `http://sysv.cn/`**（发生 HTTPS 降级为 HTTP！原因：Cloudflare `Always Use HTTPS` 未开启，且 GitHub Pages 未开启 `Enforce HTTPS`）。
   * **修复方式**：需在 Cloudflare 控制台 `SSL/TLS → Edge Certificates` 开启 **`Always Use HTTPS`**（或通过 Zone Settings 权限开启），彻底统一至 `https://sysv.cn/`。
2. **Googlebot 可达性（实测事实）**：
   * `https://sysv.cn/`、`robots.txt`、`sitemap.xml` 对 `Googlebot/2.1` 均返回干净 **HTTP 200**，未被 Cloudflare 安全规则拦截。
3. **V2.1 源码已确认的 6 项 P0 缺陷（代码事实）**：
   * `build_site.py` 首页 Title/H1 缺核心搜索词（`LLM API Pricing`、`Token Cost Calculator`），且付费模型面包屑错指 `/free-llm-apis/`。
   * `select_comparison_pairs` 无持久化注册表，且未使用排序无关主键，存在 URL 消失与 `A-vs-B / B-vs-A` 风险。
   * `build_site.py` 页面正文含每日变动的 `Last Seen: YYYY-MM-DD`，导致跨日构建时 `lastmod` 全部假刷新。
   * `fetch_data.py` 凭描述含 `self-hosted` 推定 Docker 支持、凭 Go/Rust/Python 推定 `Low-RAM VPS`，且全站多处使用未经实测的 `benchmarks` 措辞。
   * `build_compare_pages` 在模型不支持 `tools` 或 `<64K` 时仍展示 Coding Agent 费用，未显示 `Not Applicable`。
   * 缺少独立 SEO 索引质量门禁（`index`/`noindex`/`archived` 分级与 Sitemap 净化）。
