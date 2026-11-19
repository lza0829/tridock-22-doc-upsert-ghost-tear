# Tridock（到货三联台）

骨架：到货预报装载 → 实收入账 → 供应商回执装载 → 三方键对齐与差额处置 → 到货日结案封印。

- 前端 http://127.0.0.1:5900
- API http://127.0.0.1:10900

## 单号口径：同腿同单号**拒重**（全局唯一策略）

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
