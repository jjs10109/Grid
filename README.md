# 巨杉资产 · 实盘网格交易模拟

把基金经理的组合指令（Excel，自然语言价格/仓位锚点）解析后，结合**真实持仓 + 推断净值 + Wind 行情**加密成网格限价单，输出可批量导入 Oplus 的 A股/港股买卖清单，并用昨日真实分钟线模拟成交，便于逐笔核对真实交易。

- **模拟产品**：巨杉网格化
- **底层持仓来源**：净值线3号（《全球产品持仓查询》）
- **主力脚本**：`live_grid_simulator_current改.py`（单文件编排）
- **支持市场**：A股（沪/深/北交所）、港股

## 两种运行模式

| 模式 | 触发方式 | 用途 | 输出父目录 |
|---|---|---|---|
| **正常模式**（默认） | 不传 `--replay-grid-file` | 读指令 + 持仓 + 行情，从头建网格并模拟成交 | `outputs_live_grid改/`、`state改/` |
| **回放模式** | 传 `--replay-grid-file` | 读已生成的 active master 网格，跳过 WSD 建网格，只重放当天分钟线成交 | `outputs_replay_real/`、`state_replay_real/` |

> 回放模式是特殊模式，日常跑用正常模式。两种模式都按业务日期自动落到日期子文件夹，互不覆盖。

## 目录结构

```
live_grid_project/
├── live_grid_simulator_current改.py   ★ 主力脚本（正常 + 回放都在这）
├── data_loader.py                     Wind 封装 + 分钟/日级缓存读写
├── instruction_reader.py              组合指令解析
├── prefetch_minute_cache.py           分钟缓存批量预取
├── market_replay.py / compare_real_rebuilt.py   回放与核对工具
│
├── 指令/                    组合指令 + 剔除已成交产物（组合指令{short}_剔除已成交.xlsx）
├── oplus交易文件/           Oplus 真实成交（oplus{short}.xlsx）
├── 持仓数据/                全球产品持仓查询（仓位权威，.xls）
│
├── outputs_live_grid改/     ★ 正常模式输出
│   └── 网格{yyyymmdd}/      每个业务日期一个子文件夹，9 个文件
├── state改/                 ★ 正常模式状态
│   └── state{yyyymmdd}/     每个业务日期一个子文件夹，4 个文件
├── outputs_replay_real/     ★ 回放模式输出
│   └── {yyyymmdd}/          9 个文件（前缀"回放{short}真实成交"）
├── state_replay_real/       ★ 回放模式状态
│   └── {yyyymmdd}/          4 个文件
│
├── minute_cache/            分钟级 WSI 本地缓存（407 parquet）
├── wind_cache/              日级 WSD 本地缓存（41 parquet）
│
├── a股买卖清单导入模板.xlsx / 港股买卖清单导入模板.xlsx / 现货篮子指令参考模板.xlsx
├── 对账工具/、日常代码/、批量埋单文档/
├── 每日操作SOP.md、项目交接文档.md、项目完整介绍.md、项目修改记录.md、CHANGELOG.md
└── requirements.txt
```

> 显式传 `--output-dir` / `--state-dir` 时以指定路径为准，**不再追加日期子目录**；不传时才按上面的默认结构自动落日期子文件夹。

## 正常模式命令

旧口径（已验证）：`asof = 业务日 + 1`，oplus 与持仓都取业务日的前一交易日（D-1），不叠加 oplus 成交，价格带 ±30%。以业务日 1013 为例：

```powershell
& "D:\anaconda3\python.exe" ".\live_grid_simulator_current改.py" `
  --asof-date 2026-10-14 `
  --instruction-file ".\指令\组合指令1013_剔除已成交.xlsx" `
  --oplus-file ".\oplus交易文件\oplus1012.xlsx" `
  --position-file ".\持仓数据\全球产品持仓查询_20261012.xls" `
  --no-apply-oplus-trades --price-band 0.30
```

自动落到 `outputs_live_grid改/网格20261013/` 与 `state改/state20261013/`。

## 回放模式命令

读正常模式已生成的 active master 重放当天分钟线成交：

```powershell
& "D:\anaconda3\python.exe" ".\live_grid_simulator_current改.py" `
  --asof-date 2026-10-14 `
  --instruction-file ".\指令\组合指令1013_剔除已成交.xlsx" `
  --replay-grid-file ".\state改\state20261013\active_grid_master.xlsx" `
  --position-file ".\持仓数据\全球产品持仓查询_20261012.xls" `
  --no-apply-oplus-trades `
  --prefix "回放1013真实成交"
```

自动落到 `outputs_replay_real/20261013/` 与 `state_replay_real/20261013/`。

## 输出文件（每个日期子文件夹 9 个）

| 文件 | 用途 |
|---|---|
| `{前缀}_A股/港股_本次新增网格_买卖清单导入.xlsx` | **主要交付**：本次新增档位，导入 Oplus |
| `{前缀}_A股/港股_当前全部网格_买卖清单导入.xlsx` | 当前全部在挂网格 |
| `{前缀}_A股/港股_昨日分钟模拟成交_可导入.xlsx` | 昨日分钟线模拟成交清单，价格类型全 `f-指定价` |
| `{前缀}_复核报告.xlsx` | 复核报告：run_meta、持仓、position_check、流动性审计、errors 等 |
| `{前缀}_昨日分钟流动性模拟.xlsx` | 可模拟成交网格、逐档核实、分钟流动性概览 |
| `{前缀}_基金经理格式_当前全部网格.xlsx` | 按产品/股票分组的经理可读格式 |

状态目录（每个日期 4 个）：`active_grid_master.xlsx`（完整网格主表）、`current_positions.csv`（仓位快照）、`delta_ledger.xlsx`（新增去重）、`instruction_snapshot.csv`（指令摘要）。

## 本地缓存

- **分钟级 `minute_cache/`**：WSI 分钟线，命中本地非空缓存直接返回，未命中才请求并原子落盘；空结果不落盘。
- **日级 `wind_cache/`**：WSD 日线/特征，同样先查缓存；配额异常时抛错而非写空缓存。
- 缓存默认开启。可用 `--no-minute-cache` / `--no-wind-cache` 关闭，`--refresh-minute-cache` / `--refresh-wind-cache` 强制重取。
- 两个缓存目录均已纳入 git，本地与远程文件数一致。

## 版本管理（git）

- 本地 git 仓库，分支 `main`，远程 `git@github.com:jjs10109/Grid.git`。
- **本地提交与推送严格分开**：日常改动只在本地 `git add` / `git commit`；只有明确要求"上传/推送"时才执行 `git push`。
- 常用复原：`git log --oneline`、`git checkout -- 文件`、`git checkout HEAD -- 文件`、`git reset --hard 提交号`。

## 环境要求

- Python 固定 `D:\anaconda3\python.exe`（含 pywin32、WindPy）；依赖见 `requirements.txt`。
- 运行时连接 Wind 终端；`.xls` 经本机 Excel 转换，跑批前关闭所有 Excel/WPS。
