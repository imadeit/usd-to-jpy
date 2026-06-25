# USD/JPY 汇率趋势站点

这个项目维护美元兑日元的 TTB / TTM / TTS 数据。保留原有的静态 API 文件结构：

```text
YYYY/MM/DD/TTB
YYYY/MM/DD/TTM
YYYY/MM/DD/TTS
```

同时每年会生成一个年度 CSV，例如：

```text
2026/usd-jpy-2026.csv
```

CSV 列为 `日期,TTB,TTM,TTS,URL`。年度 CSV 会补齐该年份内没有汇率的日期；这些日期的 TTB / TTM / TTS / URL 保持空白。`URL` 指向有汇率日期的 Mizuho PDF，便于人工核对。

## 静态查看

直接通过静态服务器打开 `index.html` 即可查看已有 CSV 数据、趋势曲线、原始表格和 CSV 导出。页面默认显示当前年份，也可以切换到已经生成 CSV 的过去年份，或点击“加载全部数据”查看跨年份趋势。

## 本地更新服务

需要“获取最新汇率”或“修正数据”时，启动本地服务：

```bash
python3 server.py
```

默认地址：

```text
http://127.0.0.1:9343/
```

点击页面上的“获取最新汇率”后，服务会读取当前年份 CSV；如果新年 CSV 不存在，会自动创建；然后从最后一条数据之后开始抓取到当天，写入原目录结构并重建年度 CSV。

页面“导出 CSV”同样会补齐该年份内没有汇率的日期，方便后续表格分析。

趋势曲线支持鼠标滚轮缩放时间轴、拖拽平移时间范围，并可通过“重置时间轴”恢复全量视图。

页面同时维护 XAU/USD 现货黄金价格。黄金数据保存在：

```text
gold/gold-usd-all.csv
gold/gold-usd-YYYY.csv
```

点击“获取最新金价”会从 LBMA 公开价格端点下载尽可能完整的 Gold PM 历史价格，并补充最新可得价格；页面会在同一屏幕流中展示黄金指标、趋势曲线和原始数据。

黄金趋势图左轴为 `USD / troy oz`，右轴为按本地 USD/JPY TTM 折算的 `JPY / g`：

```text
JPY/g = Gold USD/oz * USDJPY TTM / 31.1034768
```

当黄金日期没有同日汇率时，页面会使用不晚于该日的最近一条 TTM。

## 命令行

从现有目录结构生成 2026 CSV：

```bash
python3 scripts/fx_rates.py build-csv --year 2026
```

更新缺失数据：

```bash
python3 scripts/fx_rates.py update --start 2026-01-01
```

查看摘要：

```bash
python3 scripts/fx_rates.py summary --year 2026
```

更新黄金价格：

```bash
python3 scripts/gold_prices.py update
```

## 数据修正日志

页面上的“修改”入口会同时更新目录文件和年度 CSV，并把修正记录追加到：

```text
logs/corrections.jsonl
```
