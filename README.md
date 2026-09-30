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
2. **工坊 chip**：仅作缸位筛选，无独立工坊 CRUD 页
3. **点缸展开**：同页内登记浸染批次、改染种、改状态、看近几笔；无平行「染缸表 / 批次表」
4. **染种配方档专页**（顶栏进入）：筛现行、新建档案、主管点现行/作废现行

**业务规则**：

- 状态改为 `ready`（可染色）时，最新批次 `redoxMv` 须已填且 ≤ -500（见 `vat_rules.py`）。
- **现行唯一**：`DyeRecipe` 同染种版本号唯一；同一染种现行档至多一条——`mark_current` 在同事务关闭同染种其它现行，并由部分唯一索引 `uniq_recipe_current_per_dye` 兜底。两人并发点同一染种现行时，至多一笔提交成功，被拒方收到 409 页面，配方专页与还原台照常可打开。染缸工可建档但不能点现行；点现行与作废现行仅主管。
- **双入口联锁**：染缸「染种字段保存」（`POST /bay/vats/{pk}/dye`）与「闲置进还原中」（`POST /bay/vats/{pk}/status`）两处入口都调用 `app/services/recipe_rules.py` 的同一个 `require_current_recipe`——染种无现行档即拒绝，禁止只拦一处。缸位条「缺现行配方档」提示与专页现行列表同源（都读 `current_recipe_map`）。

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
4. **DyeRecipe**（染种配方档）：`dye_name`、`version`、`effective_date`、`is_current`、`author`；种子数据里「合成靛」只有非现行试产档，挂在闲置缸 V-02 上，用于演示缺档联锁

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
    routers/            # auth / pages / recipes
    services/           # vat_rules.py / recipe_rules.py
    templates/          # base / bay / recipes / login
```
