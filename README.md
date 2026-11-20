# Tridock（到货三联台）

骨架：到货预报装载 → 实收入账 → 供应商回执装载 → 三方键对齐与差额处置 → 到货日结案封印。

- 前端 http://127.0.0.1:5900
- API http://127.0.0.1:10900

## 全局唯一架构：以正式表单据唯一为准（同腿同单号**拒重**）

本系统全局只贯彻一种收口架构——**正式表三腿的单号 `UNIQUE` 是最终权威**，不存在
「以回执/痕迹为准做 upsert 回滚」的第二条路。任何重复打进管道的同单号单据都按下面的
正确性口径收口：

- **双行收成一条现行**：同腿同单号的第二次及以后提交一律拒绝（HTTP 409
  `duplicate_no`），正式表始终恰一行；不做 upsert、不允许幽灵行。
- **回执与现行行同源**：只有真正落库一行才返回 `ok:true` 且回执 `id` 等于该行 id；
  被绝不返回 success，库不变，数量不被改写。
- **痕迹不悬空**：每次被拒写一条 `rejection_log`（原因码 `duplicate_no` /
  `day_sealed` / `constraint_violation`），痕迹条数与被拒事件数同源；每条痕迹指向的
  单号在正式表最多一行，不存在对不上现行行的旧值。
- **扫描禁止双计**：对账聚合按 `supplier_code + sku + dock_day` 求和，单号唯一保证
  同单据不会被吃两次；装载列表一行即库内一行。
- **封日后拒绝再动**：已结案日的新单与重复单一律 `day_sealed` 拒绝，库不变并留痕。
- **并发不留半截**：进程内写锁 + `BEGIN IMMEDIATE` + 表上 `UNIQUE` 三层收口，并发同号
  恰成功一行，其余全部被拒且留痕，无半截行。
- 前端**不得**用防抖冒充收口；所有正确性都在后端单一写路径保证。

三腿（预报 `doc_no` / 实收 `receive_no` / 回执 `confirm_no`）统一采用「同单号拒重」：

| 事项 | 口径 |
| --- | --- |
| 第二次提交同腿同单号 | **失败**（HTTP 409，原因码 `duplicate_no`），库不变 |
| 改数 | 必须使用**新单号**；不支持原地覆盖 |
| 正式三腿表 | 每条提交恰一行；表上 `UNIQUE(单号)` 是最终权威 |
| 对齐输入聚合 | 按 `supplier_code + sku + dock_day` 求和，单号唯一故不会双计 |
| 批次快照 | 任一成功装载写会让当日 `open` 批次转为 `stale` |
| `stale` 批次 | 不能结案、不能改差额命运，只能重跑对账（重跑生成新 `batch_no`） |
| 已结案日 | 新单号与重复单号**一律拒绝**（原因码 `day_sealed`），库不变 |
| 总览计数 | 按正式表行数统计，重复提交不增加笔数；另列 `rejected_count` |
| 被拒审计 | 写入 `rejection_log`，`GET /api/rejections` 与各装载页「被拒提交复查」可查 |
| 并发 | 进程内写锁 + `BEGIN IMMEDIATE` + `UNIQUE` 三层；并发同号只成功一行 |

装载回执 `POST /api/{expects,receives,confirms}` 成功时返回 `{"ok": true, "id": …, "number": …}`；
只有真正落库才会 `ok`，失败绝不返回 success。

## 启动

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 10900 --app-dir .
```

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5900
```

数据库位置默认 `data/tridock.db`，可用环境变量 `TRIDOCK_DATA`（目录）或 `TRIDOCK_DB`（文件）覆盖。

## 测试

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q
```

测试覆盖：三腿重复拒重且库不变、已封日拒写、对齐不双计、批次过期与结案闸联动、
总览计数不因重复增长、进程内与**跨进程**并发同号只落一行。
`tests/test_ghost_closure.py` 另以四个历史故障夹具（成功双行、失败却改库、痕迹悬空、
并发双落）锁定收口不变量，并联动扫描聚合、看板、装载列表与封日闸。
