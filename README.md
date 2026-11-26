# 大理石边角料拼板分拣 · yangyang-marble-ward

河埠共享石工坊用外接矩形长短边和色卡明度来配教学用料。后端接收测量 CSV，用历史台账冻结特征缩放，对符合尺寸的待分拣料建立 Ward 树，再比较不同切树簇数下能交接的套件。复核通过后，拣料员可下载成员明细和搁置清单。

## 启动

```bash
cp -n .env.example .env
docker compose build backend
docker compose up -d --no-build --wait
```

| 入口 | 地址或端口 |
|---|---|
| API | `http://127.0.0.1:19437/api` |
| 健康检查 | `http://127.0.0.1:19437/api/health` |
| OpenAPI JSON | `http://127.0.0.1:19437/api/openapi.json` |
| MySQL 宿主机端口 | `17937` |

四个 Compose 服务为 `backend`（Python 3.11、FastAPI）、`worker`（异步分拣）、`db`（MySQL 8.0）和 `redis`（任务通知）。在 `.env` 中用 `BACKEND_PORT`、`DB_PORT` 更改端口，用 `SEED_DEMO` 控制启动时导入演示数据。`JWT_SECRET` 用于签发本地令牌。容器的 MySQL 和 Redis 连接由 `DATABASE_URL`、`REDIS_URL` 配置。

手工导入或复跑演示数据可执行 `docker compose exec -T backend python -m scripts.seed_demo`。默认台账名为 `20261125-河埠石材边角料.csv`。

| 角色 | 账号 | 演示密码 | 权限 |
|---|---|---|---|
| 分析员 `analyst` | `analyst@demo.local` | `Demo-2611!` | 导入台账、提交计算、查看计算结果及下载获批 CSV |
| 复核负责人 `admin` | `admin@demo.local` | `Demo-2611!` | 分析员权限、复核签核和审计查询 |
| 查阅员 `viewer` | `viewer@demo.local` | `Demo-2611!` | 查阅台账、查看获批运行及下载获批 CSV |

## API 工作流

| 操作 | 请求 | 请求或返回内容 |
|---|---|---|
| 获取 CSV 结构和参数 | `GET /api/spec` | 输入列及顺序、运行参数及业务模块 |
| 登录 | `POST /api/auth/login` | 传 `email` 和 `password`，取得 `access_token`，其余业务请求使用 Bearer 认证 |
| 导入台账 | `POST /api/datasets` | JSON 包含 `name` 和 `csv`，其中 `csv` 是完整 CSV 文本 |
| 找到台账 | `GET /api/datasets` | 返回台账编号、名称、行数和校验和 |
| 提交运行 | `POST /api/runs` | 传 `label`、`dataset_id` 和 `settings`，空对象使用默认门槛，返回 HTTP 202 和运行编号 |
| 查看结果 | `GET /api/runs/{id}` | 等待 `status` 为 `completed`，查看 `result.tables`，`failed` 时查看 `error` |
| 签核 | `POST /api/runs/{id}/review` | 管理员提交 `{"decision":"approved","note":"已核对套件与搁置清单"}`，每次完成运行可签核一次 |
| 下载正式 CSV | `GET /api/runs/{id}/export/{key}` | 签核通过后按表键下载 |

| 表键 | 用途 |
|---|---|
| `cut_candidates` | 比较簇数、可交接件数、簇内 SSE、切树两侧合并距离 |
| `kit_members` | 每个候选的逐件归组、批次、测量值、交接状态与待核原因 |
| `ward_merges` | 完整合并顺序、左右节点、各步 Ward 距离与 SSE 增量 |
| `scaling` | 历史标尺参数及留出批均值偏移 |
| `set_aside` | 尺寸不足的单件、原始尺寸与搁置原因 |

例如，签核后用查阅员的令牌和运行编号下载成员表。按 `clusters` 筛选已复核的候选，再用 `kit_status` 和 `group_id` 分装。

```bash
curl --fail-with-body -sS \
  -H "Authorization: Bearer ${viewer_token}" \
  "http://127.0.0.1:19437/api/runs/${run_id}/export/kit_members" \
  -o kit-members.csv
```

## 边封袋边复测

初版运行复核通过后开立交接批次，封袋与复测并发进行；已封袋套件原样交接并占用套数上限，未封袋件用最新读数重建 Ward 树，新版复核通过后一次性切换，过期分组不能封袋、过期复测不能下载。

| 操作 | 请求 | 说明 |
|---|---|---|
| 开立交接批次 | `POST /api/handovers` | 传 `base_run_id` 与可选 `max_kits`（默认取建议切法的可交接套数） |
| 查看交接状态 | `GET /api/handovers/{id}` | 返回生效运行、已封袋套数/件数、剩余名额、已封袋件编号与切换日志 |
| 封袋确认 | `POST /api/handovers/{id}/seals` | 传当前生效版本的 `group_id`；成员快照即刻冻结，先确认者为准 |
| 查看封袋记录 | `GET /api/handovers/{id}/seals` | 每袋的运行版本、套件编号与成员编号 |
| 提交复测 | `POST /api/handovers/{id}/rechecks` | `csv` 必须恰好覆盖全部未封袋待分拣件，沿用冻结标尺与原门槛 |
| 复核并切换 | `POST /api/runs/{复测id}/review` | 管理员批准即在同一事务切换生效版本，返回过期组与切换后剩余名额 |

复测运行沿用普通运行的 `GET /api/runs/{id}` 查看结果；正式下载仍走 `GET /api/runs/{id}/export/{key}`，但只有交接批次当前生效版本的复测运行允许下载。复测新增表键 `kit_selection`（跨深度套件选择）、`recheck_members`（未封袋成员归组）和 `sealed_snapshot`（已封袋件快照）。选择套件时先在剩余名额内最大化交接件数，持平比较簇内 SSE 总和，再按成员编号字典序。详见 [BUSINESS.md](BUSINESS.md) 的“边封袋边复测”。


## 本地开发

领域接口位于 `backend/src/domain/project.py`。`records.py` 校验台账和尺寸，`scaling.py` 冻结历史标尺，`ward.py` 建树，`cuts.py` 比选切树和套件，`reports.py` 生成导出。角色、运行状态、复核决定分别由 `Role`、`RunStatus`、`Decision` 定义于 `backend/src/constants/workflow.py`，用于鉴权、运行详情及签核。台账用途 `train`、`holdout`、`pending` 定义于领域 `settings.py`，由导入校验和计算入口共用。

在 `backend/` 安装 `requirements.txt` 并配置数据库、Redis 和令牌密钥后，可分别运行 `python -m uvicorn src.main:app --port 19437` 和 `python -m src.worker`。只验证算法时，从批次根目录执行以下命令。oracle 只依赖 Python 标准库与本项目领域模块。

```bash
PYTHONPATH=projects/yangyang-marble-ward/backend \
  python3 test-artifacts/yangyang-marble-ward/tests/oracle.py
```

边封袋边复测的领域规则与服务工作流测试同样只依赖标准库（服务层测试通过 `tests/support.py` 注入最小依赖替身），在 `backend/` 下直接运行：

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
```


[CSV 示例、Ward 合并代价与交班剧本](BUSINESS.md)
