# /property — 房产工作流主入口

零配置版的入口是 `/home-report path.pdf`（朋友拿到能跑）。这里 `/property` 是你**日常使用**的完整工作流：拉邮件 → 入新房 → 准备看房 → 复盘 → 横向对比。

## 用法概览

```
/property                            # 默认 dry-run：扫邮件 + 列待办（不写库）
/property --apply                    # 真执行：写沟通记录、更新看房时间等

/property health                     # 检查 storage / Notion / Gmail 连通性

/property prep --addr <X> --strategy <JSON>      # 单房看房简报
/property prep --weekend                          # 列出本周末所有看房
/property prep --date YYYY-MM-DD                  # 列指定日期看房

/property review                     # 已看房源复盘 + gap 分析
/property review --shortlist         # 只看 "⭐ 感兴趣" 的

/property compare --addr A --addr B [--addr C]   # 显式地址对比
/property compare --weekend                       # 本周末看房全对比
/property compare --status "⭐ 感兴趣" [--ranking <JSON>]

/property emails --hours 72          # 仅跑 email intake (dry-run)
/property analyze <PDF>              # 等价于 /home-report
```

任何子命令最终都是调：
```bash
python -m property_assistant.orchestrator.router <subcommand> <args>
```

---

## 第一步：健康检查（每次执行前先跑）

```bash
python -m property_assistant.orchestrator.router health
```

输出 `✅ backend=notion · db ... reachable` 或 `❌ ...`。失败时按以下降级：
- Notion 不通 → 把 `STORAGE_BACKEND=local` 临时导出再继续
- Gmail 没配 → email 相关子命令告知用户"先在 .env 配 Gmail 才能跑 intake"
- 都正常 → 直接进第二步

## 第二步：意图识别（自然语言 → 子命令）

如果用户输入是**精确子命令**（"prep --weekend" / "compare A B" 等），**直接传给 router.py**。

如果是**自然语言**，按下面映射表选 pipeline。落不到任何映射就**显式回问用户**，绝不猜：

| 用户说 | 路由到 |
|---|---|
| 包含 PDF 路径 / "分析这个 home report" | `analyze <path> --opinion <生成的 opinion.json>` |
| "看房前准备" / "周末看房" / "明天看房" | `prep --weekend` 或 `prep --date <推断>` |
| "复盘" / "总结看过的" / "review" | `review` |
| "对比 A 和 B" / "横向比较" / "shortlist 对比" | `compare --addr A --addr B` |
| "邮件" / "收件" / "email" | `emails --hours 48` |
| 没头没尾的 "/property"，没参数 | 默认子命令（intake dry-run） |
| 模糊（"看看这个房子"无 path） | 显式回问："是要 (a) 分析 PDF (b) 看房前简报 (c) 复盘已看过的？" |

## 第三步：默认子命令（无参数 `/property`）

执行：
```bash
python -m property_assistant.orchestrator.router
```

这会跑 intake **dry-run**（默认不写库），输出大致格式：

```
🔍 DRY-RUN · 处理了 7 封邮件

📬 匹配到房源 (5 封):
  · [viewing_confirmed] Viewing confirmed for Saturday 11am
      → 10 Marchmont Rd · would: append comm entry + set viewing_date=Sat 11:00

📄 发现新 PDF (2):
  · ~/Downloads/HR_24_Forth_St.pdf
  · ~/Downloads/HR_88_Polwarth.pdf

❌ 未匹配 (2 封)

💡 建议下一步:
  · /home-report ~/Downloads/HR_24_Forth_St.pdf
  · /home-report ~/Downloads/HR_88_Polwarth.pdf
  · /property --apply  (to actually write the matched updates above)
```

把这个原样展示给用户。如果用户确认 "去做"，就追加 `--apply` 重跑。

## 第四步：各子命令的额外注意事项

### `prep --addr <X> --strategy <JSON>`
单房简报。如果用户只说"准备 Marchmont 看房"，你需要：
1. 先在 storage 里 `find_by_address("Marchmont")` 看是否已入库（router 会做）
2. 如果有 PropertyRecord 但没 SurveyorOpinion JSON 缓存 → 提示用户"先 `/home-report` 跑过吗？我可以现在生成 opinion + strategy"
3. 让 Claude **生成 ViewingStrategy JSON**（按 `python -m property_assistant.analysis.viewing_strategy schema` 输出的 schema），写到 `/tmp/strategy_$$.json`
4. 校验：`python -m property_assistant.analysis.viewing_strategy validate --strategy /tmp/strategy_$$.json`，失败重试 1 次
5. 跑 router：`python -m property_assistant.orchestrator.router prep --addr "Marchmont" --strategy /tmp/strategy_$$.json [--opinion <opinion.json>] [--viewing-time <T>] [--agent <A>]`

### `prep --weekend`
列出本周末所有看房（Sat+Sun），不自动生成简报（因为每个都需要 strategy JSON，应该让用户决定哪些跑）。给一个清单后建议："要为哪些生成 brief？我可以挨个跑 /property prep --addr <X>"。

### `compare`
如果 `--addr` 没给，但用户说"对比 shortlist"或"本周末的房子" → 用 `--status "⭐ 感兴趣"` 或 `--weekend` 代替。
可选 ranking：用户问"哪个最好" → 生成 PropertyRanking JSON（schema 见 `python -m property_assistant.analysis.comparison schema --n <N>`）→ 校验 → 传 `--ranking`。

### `emails`
等同默认子命令但可指定 `--hours N`。同样 dry-run 默认。

### `analyze` = `/home-report`
入口完全等价。Claude 在 SKILL.md 里走 `commands/home-report.md` 的完整流程（解析 → 生成 opinion JSON → 校验 → 跑 pipeline）。

## 第五步：终端输出格式约定

- 所有子命令都直接 print 到 stdout，不要包成 JSON（用户读着方便）
- 例外：`review --json` / `emails --json` 时输出结构化 JSON，方便链式调用
- 错误：写 stderr，return code 非 0
- 看完总结后，如果有 next-step 建议（intake / review 都会给），把建议**作为可点击的命令片段**贴在输出末尾

## 设计原则（避免回退）

1. **router.py 只做精确解析**，不做自然语言路由。NL 由你（Claude 在 SKILL.md 里）判断
2. **默认 dry-run**：写库类操作必须显式 `--apply`
3. **失败可降级**：Notion 不通自动 fallback local；Gmail 没配跳过 email 步骤
4. **每个 pipeline 都有 CLI**，可以单独调试（不必走 router）
5. **不猜测**：意图不明就回问，不要瞎选一个 pipeline 跑
