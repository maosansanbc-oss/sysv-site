# SysV Radar V2.1 — 英文搜索需求、竞争缺口与现金变现合规调研

> **调研原则**：所有未接入付费关键词工具（Ahrefs / Semrush）或 Google Search Console 实测的搜索量一律如实标注为 **未知**，严禁编造月搜索量或预期收益。

---

## 一、英文市场候选关键词与竞争缺口分析

| 候选关键词（英文） | 真实用户意图 | 搜索量 | 现有主要竞争者 | 现有页面痛点 / 我们的独有切入价值 | 数据获取成本 | 商业变现路径 |
| :--- | :--- | :---: | :--- | :--- | :---: | :--- |
| `free LLM API without credit card` | 零预算跑通 AI 原型/脚本，明确拒绑信用卡 | 未知 | KDnuggets、FlatKey、博客合集 | 大多是静态博文，过时快（如不少 2025 文章仍在推已于 2026-07 下线的 GitHub Models），且常把 OpenRouter 动态路由误写成免费。**我们每日实测更新 + 明确标出 RPM/RPD 限制与绑卡要求**。 | 极低（OpenRouter API + 少量人工核验） | AdSense + 云服务/AI Gateway 联盟 |
| `LLM API price history` / `LLM price drops` | 观察模型降价周期，决定现在买还是等降价 | 未知 | BenchLM、CostGoat、零散博客 | 多数站点只展示“今日价格”，缺乏持续归档的每日变动日志。**我们从 2026-11-24 起每日归档全量 473 端点快照，时间越久壁垒越高**。 | ¥0（每日自动快照） | AdSense + B2B 开发者工具联盟 |
| `cheapest LLM API for bulk translation` | 批量翻译百万字文档，追求极低成本与非实时批处理 | 未知 | Novita / IntuitionLabs 厂商软文 | 厂商软文只推自家模型，很少把 **异步 `:batch` 定价 + Cache Read + 多厂商横向对比**放进同一个计算器。 | ¥0（OpenRouter 含 92 个 `:batch` 端点） | AdSense + API 网关 / 云厂商现金联盟 |
| `cheapest API for AI agents` | 降低多轮 Agent 调用费，要求必须支持 Tool Calling | 未知 | LLMPrice、TokenCost、Reddit 讨论 | 普通价格表不筛选 `tools` 与上下文门槛，容易把不能调工具或仅支持 batch 的模型排在第一。**我们按 `is_realtime + tools + >=64K` 能力门控过滤**。 | ¥0（OpenRouter `supported_parameters`） | AdSense + 云主机/VPS 现金联盟 |
| `cheapest VPS for self-hosted n8n` | 用 3–6 美元/月低配 VPS 跑通工作流替代 Zapier | 未知 | Hostinger/Contabo 测评站、Reddit | 高商业意图（高转化），但需维护真实 VPS 价格与内存测试数据；不要凭空写“免费 VPS”水文。 | 中（需定期核验 6–10 家真实 VPS 报价） | **高现金佣金**（DigitalOcean CJ / Vultr / Railway） |
| `free OpenRouter alternatives` | 寻找 OpenRouter 限流或抽成后的替代网关/自托管方案 | 未知 | LiteLLM 文档、开发者博客 | 适合结合我们的自托管 AI Gateway 开源目录（LiteLLM、One-API 等）做决策对比。 | ¥0（GitHub API） | 云 VPS 现金联盟 + AdSense |

**本阶段聚焦策略（克制扩张）**：不盲目铺 20 个新栏目，优先靠 **GSC（Google Search Console）实测数据** 验证现有 4 个核心入口（`/` 任务成本计算器、`/free-llm-apis/`、`/price-changes/`、`/zero-cost-stack/`）的自然曝光与点击，再决定往哪个高转化词深挖。

---

## 二、现金结算联盟（Cash Payout Affiliates）真实核查

> **铁律**：仅把**可提现至银行卡/PayPal 的现金佣金**计入收入；平台赠送额度（Account Credits）**一律不计入收入**。

| 平台 | 佣金模式 | 现金提现门槛 | 现金收款方式 | 关键避坑与核查结论 |
| :--- | :--- | :---: | :--- | :--- |
| **DigitalOcean（经由 CJ Affiliate）** | 现金联盟（区分于普通邀请链接） | 低 / 视 CJ 结算设置 | PayPal / 电汇 / Direct Deposit | **注意区分**：官网普通 Referral 链接只给平台余额（不算钱）；必须申请 **CJ Affiliate 官方项目** 才能拿现金佣金。 |
| **Vultr** | 官方自营 CPA 现金联盟 | **\$100**（余额无门槛） | PayPal（现金） | 需明确在后台选择 PayPal 现金结算，注意有效付费用户留存考核期（防佣金撤销 Clawback）。 |
| **Railway** | 官方 15% 收入分成（首 12 个月） | **\$100** | 后台支持的现金提现渠道 | 非常契合“自托管 n8n / AI 工具一键部署”用户群，持续分成属性强。 |
| **Hetzner** | **已永久关停** | — | — | **官方已停止旧版联盟/推荐计划**，不要浪费时间做 Hetzner 联盟引导。 |

---

## 三、Google AdSense 收款与内地 / 香港银行卡合规硬核查

1. **提现门槛**：美元账户常规门槛 **\$100**，港币账户 **HK\$800**。
2. **严格的“同国家/地区一致”红线**：
   * Google AdSense **严禁跨区收款**：账号注册国家/地区、税务居住地、PIN 码邮寄地址、身份验证证件与收款银行所在地区**必须完全一致**。
   * **中国内地居民持有香港银行卡（汇丰香港 / 恒生 / 建行亚洲 / ZA Bank）≠ 可以直接开香港个人 AdSense**：
     * 如果你实际常住中国内地且无合法香港居民身份证与香港住址证明，注册香港个人 AdSense 会在身份验证/税务审核/PIN 环节触发冻结，资金可能滞留无法取出。
     * **合规路径二选一**：
       1. **内地个人主体**：注册中国内地 AdSense，绑定本人名下支持跨境商业电汇收款的内地银行账户（遵守外管局 SAFE 结汇与个税申报要求）。
       2. **香港商业主体**：如未来规模化且已依法设立香港有限公司，使用香港公司商业主体注册香港商业 AdSense，绑定对应香港公司对公账户与香港商业登记证。
3. **现阶段结论（为什么先看现金联盟，再审慎开 AdSense）**：
   * 在月流量未突破 10,000 PV 前，AdSense 可能需要数月甚至近一年才能攒够 \$100 门槛，且注册地区一旦选错极难更改。
   * **建议先在 GSC 验证真实自然搜索流量，并优先测试合规的现金结算联盟（如 CJ/DigitalOcean、Vultr、Railway），等流量起量并确认收款主体方案后再提交 AdSense。**

---

## 四、经营数据闭环（13 项核心指标）

每日自动化运行记录在 [`data/ops_metrics.json`](../data/ops_metrics.json) 中，人工每周补充 GSC 与联盟真实数据：
- **自动化生产层**：`fetch_success`、`publish_success`、`llm_calls`、`llm_tokens_in`、`llm_tokens_out`、`llm_cost_usd`
- **获客层**：`visits`、`gsc_impressions`、`gsc_clicks`、`indexed_pages`
- **变现与结算层**：`affiliate_clicks`、`affiliate_conversions`、`estimated_revenue_usd`、`confirmed_payable_usd`、`bank_settled_usd`、`net_profit_usd`
- **人工投入层**：`manual_interventions`、`manual_minutes`
