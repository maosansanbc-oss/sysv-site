# SysV Radar (`sysv.cn`) — Zero-Cost Automated Static Site Infrastructure

全自动零成本静态站基建：**GitHub Actions 每日定时抓公开数据 → 免费大模型 API 增量加工（多源自动降级）→ 纯 Python 编译静态站 → Cloudflare Pages 自动部署到 `sysv.cn`**。

## 目录结构

- `site.config.json`：全站配置（域名、每日 LLM 加工上限、Google AdSense ID、Affiliate 联盟链接）
- `scripts/fetch_data.py`：拉取 OpenRouter 实时模型定价（免 Key）+ GitHub 开源基础设施热门仓库
- `scripts/llm_router.py`：多源免费大模型路由器（Gemini / Groq / OpenRouter / SiliconFlow 自动切换防挂）
- `scripts/enrich_llm.py`：增量 AI 结构化加工（缓存到 `data/*.json`，不重复消费额度）
- `scripts/build_site.py`：零依赖纯 Python 静态站生成器（含交互式 Token 计算器、筛选表、pSEO 对比页、Sitemap、AdSense 合规站务页）
- `.github/workflows/daily-auto.yml`：每日定时自动跑通与 Cloudflare Pages 部署

## 本地一键验证

```bash
python3 -m scripts.fetch_data
python3 -m scripts.enrich_llm
python3 -m scripts.build_site
python3 -m http.server 8000 --directory dist
```

## 上线 `sysv.cn` 只需 4 步

1. **创建 GitHub 仓库**，把本目录推送到 `main` 分支。
2. **在 Cloudflare Pages 创建项目**（项目名填 `sysv-site`），并在 Custom Domains 中绑定 `sysv.cn`（因为你的域名已在 Cloudflare，会自动配好 DNS + HTTPS）。
3. **在 GitHub 仓库 → Settings → Secrets and variables → Actions 中添加 Secrets**（切勿写进代码）：
   - `CLOUDFLARE_API_TOKEN`（权限选 `Cloudflare Pages: Edit`）
   - `CLOUDFLARE_ACCOUNT_ID`（Cloudflare 控制台右侧栏可见）
   - 至少 1 个免费大模型 Key（建议配 2 个互为备份）：`GEMINI_API_KEY` / `GROQ_API_KEY` / `OPENROUTER_API_KEY` / `SILICONFLOW_API_KEY`
   - 可选：`ALERT_WEBHOOK_URL`（飞书/Telegram/Slack 失败告警）
4. **手动触发一次测试**：进入 GitHub 仓库 `Actions` → `Daily Auto Update & Deploy (sysv.cn)` → 点击 `Run workflow`。

## 变现接入（上线稳定跑通 1~2 周后）

1. 去 [Google Search Console](https://search.google.com/search-console) 添加 `sysv.cn`，提交 `https://sysv.cn/sitemap.xml`。
2. 在 `site.config.json` 的 `monetization.affiliates` 中替换为你自己的云厂商/AI 工具联盟返佣链接（立即可用）。
3. 收录正常后申请 Google AdSense，审核通过后把 `adsense_client_id` 与广告位 ID 填入 `site.config.json`，程序会自动注入广告代码与 `/ads.txt`。
