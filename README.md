# 博物馆展品光照剂量审计 (Museum Light Dose Audit)

用 FastAPI + SQLite + 原生前端实现的展品光照剂量审计工具:

- **梯形积分累计剂量**:上传展柜带时间戳的照度 JSONL 与展品在柜时间段,按相邻读数梯形积分累计 lux·hour;
- **断点证据**:相邻读数间隔超过阈值(默认 6h,可调)的区间被明确标记为"跨断点区间",仍计入剂量但在曲线和证据表中单独标红;
- **轮换模拟**:模拟把展品移入另一展柜,按两柜历史平均日剂量速率分别预测达到年度剂量上限的日期,给出轮换收益天数;
- **并列展示**:剂量曲线、断点证据、轮换前后对比同页呈现,可一键导出审计 JSON;
- **零外部依赖**:无需任何第三方账号,SQLite 单文件存储,默认仅绑定本机回环地址。

## 快速开始

### Docker(推荐,单条命令)

```bash
docker compose up --build
```

打开 <http://localhost:8000>。端口映射为 `127.0.0.1:8000:8000`,默认仅本机可访问;
SQLite 数据持久化在命名卷 `audit-data` 中。

### 本地 Python(无需 Docker)

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 演示数据

页面右上角点 **「载入演示数据」** 即可一键生成:两个展柜 120 天照度曲线(含两处传感器离线断点)+
一件绢本展品(年上限 50 000 lux·h,已用约 87%),随后点「运行审计」「模拟轮换」查看完整流程。

也可命令行生成 JSONL 手动上传:

```bash
python3 scripts/make_sample_data.py   # 输出 sample_data/case_a.jsonl, case_b.jsonl
```

## 使用流程

1. **展柜与照度数据**:添加展柜 → 上传该柜照度 JSONL(每行一条):

   ```json
   {"ts": "2026-01-01T08:00:00Z", "lux": 152.3}
   ```

   重复时间戳自动去重,坏行按行号报错,不影响其余行导入。

2. **展品与在柜时间段**:添加展品(设置年度剂量上限,默认 50 000 lux·h,对应高度光敏材质的常见限值)→
   登记在柜时间段(结束时间留空并勾选「至今在柜」表示仍在展出)。

3. **剂量审计**:选择展品与断点阈值 → 运行审计。页面并列显示:
   - 累计剂量曲线(红色区段 = 跨断点区间,虚线 = 年度上限);
   - 断点证据表(展柜、区间、时长、该区间计入的剂量);
   - 汇总统计(累计剂量、上限占用百分比、断点数)。

4. **轮换模拟**:选择目标展柜 → 对比「留在当前柜」与「移入目标柜」的预计达限日期与剩余天数。

5. **导出**:点击「导出审计 JSON」下载含全部区间明细的审计文件。

## 计算方法

- **剂量积分**:相邻读数 `(t1, lux1)`、`(t2, lux2)` 之间按梯形面积累计
  `dose = (lux1 + lux2) / 2 × Δt(小时)`;窗口边界处对读数做线性插值。
- **断点标记**:`Δt > gap_hours`(默认 6,页面可调)的区间标记为跨断点区间,
  剂量仍按梯形法计入,但在证据表与曲线中单独标出,供审计人员判断可信度。
- **无数据时段**:在柜时间段超出读数覆盖范围的部分不做外推,以 `uncovered` 列入审计 JSON。
- **达限预测**:展柜日剂量速率 = 历史总剂量 ÷ 数据覆盖天数;
  预计达限日期 = 今天 + (年度上限 − 当前累计剂量) ÷ 目标柜日剂量速率。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/cases` | 创建展柜 |
| GET | `/api/cases` | 展柜列表(含读数统计、平均日剂量) |
| POST | `/api/cases/{id}/readings` | 上传照度 JSONL(multipart 文件) |
| POST | `/api/exhibits` | 创建展品(含年度上限) |
| POST | `/api/exhibits/{id}/placements` | 登记在柜时间段 |
| GET | `/api/exhibits/{id}/audit?gap_hours=6` | 剂量审计(曲线、区间、断点证据) |
| POST | `/api/simulate` | 轮换模拟 `{exhibit_id, target_case_id, gap_hours}` |
| GET | `/api/exhibits/{id}/export?gap_hours=6` | 导出审计 JSON(附件下载) |
| POST | `/api/demo` | 一键载入演示数据 |

交互式 API 文档:<http://localhost:8000/docs>

## 项目结构

```
app/
  main.py          FastAPI 路由与审计/模拟/导出逻辑
  dose.py          梯形积分、断点标记、达限预测(纯函数,可单测)
  database.py      SQLite 连接与建表(标准库 sqlite3)
  static/          原生前端(index.html / app.js / style.css,Canvas 绘图)
scripts/make_sample_data.py   示例数据生成器
sample_data/       生成的示例 JSONL
Dockerfile / docker-compose.yml
```

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DOSE_DB_PATH` | `./data/audit.db`(容器内 `/srv/data/audit.db`) | SQLite 文件路径 |

如需对外提供访问,修改 `docker-compose.yml` 中的端口映射为 `"8000:8000"`(默认 `127.0.0.1:8000:8000` 仅本机)。
