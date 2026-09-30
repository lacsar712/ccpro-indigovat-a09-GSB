# IndigoVat-01 · 染缸还原台

FastAPI + PostgreSQL + Jinja2：主界面是横向**缸位条**（Alpine 反应式），不是工坊/染缸/批次三表导航。Session Cookie 登录；规则在 `app/services/vat_rules.py`。

## 技术栈

- FastAPI、SQLAlchemy 2、PostgreSQL
- 启动时 `create_all` + 幂等种子（蓝靛湾一号坊 / 清水江二号坊）
- Session Cookie 认证（Starlette SessionMiddleware）
- Jinja2 + Alpine.js + Pico（叠靛蓝水墨自定义样式）
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4720** |
| Postgres | **6120**（容器内 5432） |

数据库账号：`indigovat` / `indigovat` / 库名 `indigovat`

## 快速启动

```bash
cd IndigoVat/IndigoVat-01
docker compose up --build -d
```

浏览器打开：http://localhost:4720

演示账号（登录页已预填）：

- `admin` / `123456`
- `worker` / `123456`

## 交互（信息架构）

1. **染缸还原台**：横滑缸位条，每缸显示状态、最近电位与 redox sparkline
2. **染种配方专页**（顶栏入口）：配方档列表、筛「仅现行」/染种、新建配方、主管点现行/作废
3. **工坊 chip**：仅作缸位筛选，无独立工坊 CRUD 页
4. **点缸展开**：同页内登记浸染批次、改状态、看近几笔；无平行「染缸表 / 批次表」
5. **新建染缸**：缸位条下方「＋ 新建染缸」建档，默认闲置

**业务规则**：

- 状态改为 `ready`（可染色）时，最新批次 `redoxMv` 须已填且 ≤ -500（见 `vat_rules.py`）。
- **现行配方档双入口联锁**：染种配方档（`DyeRecipe`）字段为染种名、版本号、生效日、是否现行、编制人。**同染种版本号唯一；同一染种现行档至多一条。**
  - 染缸工可建档（配方档/染缸），但**不能点现行**；只有**主管**（`admin`）可点现行，点现行时在**同一事务**内关闭同染种其它现行档；**作废现行也仅主管**。
  - 两人几乎同时点同一染种现行：靠 `SELECT … FOR UPDATE NOWAIT` 行锁串行化 + 数据库**部分唯一索引**（`uniq_current_recipe_per_dyetype`，仅对现行行生效）兜底，至多一条成功；被拒方收到提示，配方专页与还原台仍可正常打开。
  - **保存染缸**与**闲置改还原中**两处入口都调用同一个函数 `app/services/recipe_rules.py::assert_dyetype_has_current`：染种无现行档即拒绝，禁止只拦一处。缸位条上的「缺现行配方档」提示与配方专页现行列表同源（均取自 `current_recipe_map`）。
  - 种子数据中 `蓼蓝草靛` 只有一条未现行草稿，并挂闲置缸 `V-21`，用于演示缺档拦截。

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6120
uvicorn app.main:app --host 0.0.0.0 --port 4720 --reload
```

## 业务模型

1. **Workshop**：`name`、`region`、`notes`（UI 上仅为筛选片）
2. **Vat**：归属工坊、`code`、`dyeType`、`volumeL`、状态 `idle|reducing|ready`
3. **DipLot**：归属染缸、`dippedAt`、`clothMeters`、`redoxMv`（可空）
4. **DyeRecipe**：`dyeType`、`version`、`effectiveDate`、`isCurrent`、`preparedBy`；`(dyeType, version)` 唯一，部分唯一索引保证同染种现行至多一条

## 目录结构

```
IndigoVat-01/
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  requirements.txt
  app/
    main.py
    db.py
    models.py
    schemas.py
    auth.py
    seed.py
    routers/
      auth.py / pages.py（还原台、保存染缸、改状态）/ recipes.py（配方专页）
    services/vat_rules.py / recipe_rules.py
    templates/   # base / bay / login / recipes
```
