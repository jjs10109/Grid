# 每日网格操作 SOP（照着做即可）

> 主力脚本：`live_grid_simulator_current改.py`。模拟产品=巨杉网格化，底层持仓=净值线3号。
> 日常跑**正常模式**；只有需要重放某天真实分钟成交时才用**回放模式**。

## 0. 跑前准备

- 确认 Wind 终端已登录（首次取数需要；缓存命中时不连 Wind）。
- **关闭所有 Excel/WPS**，否则 `.xls` 持仓文件经 COM 转换会失败。
- Python 固定用 `D:\anaconda3\python.exe`。
- 缓存默认开启，无需额外操作。

---

## 1. 正常模式（日常）

在项目根目录执行。以业务日 1013 为例：

```powershell
& "D:\anaconda3\python.exe" ".\live_grid_simulator_current改.py" `
  --asof-date 2026-10-14 `
  --instruction-file ".\指令\组合指令1013_剔除已成交.xlsx" `
  --oplus-file ".\oplus交易文件\oplus1012.xlsx" `
  --position-file ".\持仓数据\全球产品持仓查询_20261012.xls" `
  --no-apply-oplus-trades --price-band 0.30
```

### 六天参数对照（旧口径，已验证）

| 业务日 | asof-date | oplus 文件 | 持仓文件 | 活跃网格/可模拟成交 |
|---|---|---|---|---|
| 1013 | 2026-10-14 | oplus1012 | 全球产品持仓查询_20261012 | 165 / 22 |
| 1014 | 2026-10-15 | oplus1013 | 全球产品持仓查询_20261013 | 172 / 11 |
| 1015 | 2026-10-16 | oplus1014 | 全球产品持仓查询_20261014 | 161 / 6 |
| 1016 | 2026-10-17（周六） | oplus1015 | 全球产品持仓查询_20261015 | 156 / 8 |
| 1019 | 2026-10-20 | oplus1016 | 全球产品持仓查询_20261016 | 129 / 6 |
| 1020 | 2026-10-21 | oplus1019 | 全球产品持仓查询_20261019 | 133 / 9 |

口径要点：
- **asof = 业务日 + 1**（含周末，asof 周六会自动回取周五工作日）。
- oplus、持仓都取业务日的**前一交易日**文件。
- 必加 `--no-apply-oplus-trades`（持仓已含前一天成交，不重复叠加）。
- `--price-band 0.30` 只输出参考价 ±30% 内档位。

跑完自动落 `outputs_live_grid改/网格{yyyymmdd}/`（9 文件）与 `state改/state{yyyymmdd}/`（4 文件）。

---

## 2. 回放模式（重放真实分钟成交）

读正常模式已生成的 active master，跳过 WSD 建网格，只重放当天分钟线。以 1013 为例：

```powershell
& "D:\anaconda3\python.exe" ".\live_grid_simulator_current改.py" `
  --asof-date 2026-10-14 `
  --instruction-file ".\指令\组合指令1013_剔除已成交.xlsx" `
  --replay-grid-file ".\state改\state20261013\active_grid_master.xlsx" `
  --position-file ".\持仓数据\全球产品持仓查询_20261012.xls" `
  --no-apply-oplus-trades `
  --prefix "回放1013真实成交"
```

跑完自动落 `outputs_replay_real/{yyyymmdd}/` 与 `state_replay_real/{yyyymmdd}/`。

> 回放必须显式传对应日期的 `--instruction-file`，不能留空。
> PowerShell 里变量后直接接中文要用 `${变量}` 界定，例如 `--prefix "回放${short}真实成交"`。

---

## 3. 结果核对

- 打开输出目录里的 `{前缀}_复核报告.xlsx`，看 `liquidity_sim_audit`（成交审计）和 `errors`：六天 errors 全 0。
- 可导入文件的价格类型应全为 **`f-指定价`**。
- 与 `oplus交易文件/oplus{业务日}.xlsx` 逐笔对照（证券代码、委托方向、成交数量、成交均价）。六天回放与正常模式成交逐笔一致。
- 注意 oplus 的"最新价"列是导出时点现价，不一定是业务当天价；以"成交均价"核对业务当天真实价。

---

## 4. 本地缓存

- 分钟线 `minute_cache/`（407 parquet）、日线 `wind_cache/`（41 parquet），默认先查本地。
- 同参数二次运行全部命中缓存、不再连 Wind。
- 强制重取：`--refresh-minute-cache` / `--refresh-wind-cache`；关闭缓存：`--no-minute-cache` / `--no-wind-cache`。

---

## 5. 版本管理（本地）

- 跑完确认无误后，在本地提交形成版本：
  ```powershell
  git add -A
  git commit -m "说明本次改动"
  ```
- **不要自动推送**。只有明确收到"上传/推送到 GitHub"指令时才执行 `git push`。
- 需要回退用 `git log --oneline` 找到提交号，再 `git reset --hard 提交号` 或 `git checkout HEAD -- 文件`。
