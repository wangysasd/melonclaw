# pgvector 安装与升级指南

本说明是数据库侧准备的唯一独立文档，覆盖：

- **首次安装**：安装 PostgreSQL 本体、创建账号和库、安装 pgvector、执行 `CREATE EXTENSION vector`。
- **已有部署升级**：生产库要新增或确认 `vector` 扩展时的操作顺序与权限处理。

首次部署按第 2 节操作，已有部署接入知识库按第 3 节操作。项目脚本 `ops/setup-pgvector.sh` 用于检查并启用扩展，参数可用 `--help` 查看。

## 1. 先理解三件事

1. **pgvector 分两层**。第一层是操作系统里的扩展文件（`vector.so`／`vector.control`），由包管理器安装，整个 PG 实例装一次。第二层是数据库里的 `CREATE EXTENSION vector`，**按数据库生效**，换库或新建库都要在目标库再执行一次。
2. **`CREATE EXTENSION` 不改业务数据**。它在目标库登记扩展和类型，不删表、不改已有行。`IF NOT EXISTS` 保证重复执行安全。生产上仍按数据库变更流程安排备份和停写窗口。
3. **应用账号通常不需要建扩展的权限**。扩展启用后，连接该库的账号都能使用 `vector` 类型。只在启用那一步需要超级用户（或数据库 `CREATE` 权限，取决于平台是否把 pgvector 标记为 trusted）。最稳妥的做法是让管理员执行一次，应用账号只负责用。

## 2. 首次安装（新用户）

顺序不能颠倒：先有库和账号，再由脚本在**那个库**里启用扩展，最后才初始化应用 schema。

### 2.1 安装 PostgreSQL、创建账号与库

先安装 PostgreSQL 14+ 与 `psql`／`pg_config` 客户端，并启动服务：

- **macOS / Homebrew**：`brew install postgresql@18`，然后 `brew services start postgresql@18`。若使用别的版本，保持 `psql`、`pg_config` 与服务端指向同一个主版本。
- **Linux / Ubuntu**：`sudo apt-get install -y postgresql postgresql-client`，然后 `sudo systemctl start postgresql`。发行版仓库版本低于 14 时，先按 PostgreSQL 官方仓库说明配置对应主版本源。若计划用包管理器安装 pgvector，也建议先配置该源。

验证：

```bash
psql --version
pg_config --version
pg_config --pkglibdir
```

记下 PG 主版本号，第 2.3 节的 pgvector 包名会用到它。`ops/setup-pgvector.sh --check` 会报告当前角色是否为超级用户，据此按第 1 节判断是否需要管理员角色。

再按 [PostgreSQL 准备指南](../docs/postgresql-setup.md) 创建应用账号和数据库、验证连接与建表权限。`CREATE EXTENSION` 需要的权限说明见第 1 节，Linux 上通常由管理员角色执行一次即可，不要长期扩大应用账号权限。

脚本**不建库、不建账号**。如果这一步没做，脚本会在连接目标库时明确失败。

### 2.2 填写应用配置

按 [README](../README.md#配置文件) 在 `.env` 填写 `DATABASE_URL`。脚本只从配置里提取**库名**，不输出连接串、密码或文件其它内容。

### 2.3 安装 pgvector 扩展文件

先体检，看本机 PG 版本和扩展文件状态：

```bash
bash ops/setup-pgvector.sh --check
```

若输出「pgvector 扩展文件: 未找到」，按平台安装：

- **macOS / Homebrew**：`brew install pgvector`。也可以让脚本代劳，见 2.4 的 `--install`。
- **Linux / Ubuntu**：`sudo apt-get install -y postgresql-<PG主版本>-pgvector`。仓库没有对应包时，先装 `postgresql-server-dev-<PG主版本>`，再按 `--check` 打印的源码编译命令执行。编译必须用当前 `pg_config`，否则扩展会落到另一个 PG 版本目录。

装完再跑一次 `--check`，确认「pgvector 扩展文件: 已就位」。

### 2.4 在目标库启用扩展

```bash
bash ops/setup-pgvector.sh --check
bash ops/setup-pgvector.sh --apply
```

本机 Homebrew 若还没装扩展文件，可以一条命令完成：

```bash
bash ops/setup-pgvector.sh --apply --install
```

需要管理员角色时加 `--admin-user <角色>`。跨主机或应用账号与当前系统用户不同时，设置 `PGHOST`／`PGPORT`／`PGUSER`，认证用 `.pgpass` 或已导出的 `PGPASSWORD`；不要把密码写进命令行。

看到「vector(1024): 可用」即完成。

### 2.5 初始化应用并启动

```bash
uv run melonclaw-resources --service-stopped install-builtins --source .data/skills/shared
uv run melonclaw-db-init
```

`melonclaw-db-init` 只用于首次空库，不会自动清库。之后按 README 启动服务。

## 3. 已有部署升级（生产）

按 [部署说明](../docs/deployment.md) 与 [发版手册](DEPLOY.md) 的停写、备份、恢复规则执行。本节只补充 `vector` 扩展的位置。

1. **确认变更类型**。涉及数据库结构时走含 `melonclaw-db-update` 的更新流程，见 [DEPLOY.md 第 4 节](DEPLOY.md)。
2. **停服并备份**。停止 Web（含任务执行器），备份数据库、数据目录、工作区与外部配置。备份含凭据，限制访问权限。
3. **以服务器账号进入代码目录**，并让脚本读到生产配置：

   ```bash
   cd /opt/melonclaw
   export MELONCLAW_ENV_FILE=/etc/melonclaw/melonclaw.env
   bash ops/setup-pgvector.sh --check
   ```

   `--check` 只读，可直接用于上线前确认。它会报告 PG 版本、扩展文件、目标库连接、当前角色是否超级用户、扩展版本和 `vector(1024)` 是否可用。
4. **扩展文件未就位时**，由管理员按第 2.3 节的 Linux 步骤安装。脚本不会静默执行 `sudo` 编译。
5. **在目标库启用扩展**：

   ```bash
   bash ops/setup-pgvector.sh --apply
   ```

   若「vector 扩展: 已启用」，这一步只是 `IF NOT EXISTS` 加复验，不产生新变更。需要管理员角色时加 `--admin-user postgres`；peer 认证要求系统用户与数据库角色一致时，请以对应系统账号执行，或用 `--admin-user` 配合已配置的认证，不要修改服务账号权限来绕过。
6. **执行本次版本的数据库升级**（仅当版本包含 schema 变更）：

   ```bash
   MELONCLAW_ENV_FILE=/etc/melonclaw/melonclaw.env \
     .venv/bin/melonclaw-db-update
   ```

   脚本本身不执行 `db-update`，两者顺序由本流程保证。不要用 `melonclaw-db-init` 代替升级。
7. **启动服务并检查就绪**，按 [DEPLOY.md 第 4/5 节](DEPLOY.md) 完成 `ready=true` 与功能验证。

升级失败时保持服务停止，不要继续后续步骤。代码回滚不会撤销数据库变更。

### 已启用 pgvector，需升级扩展版本

首次接入知识库通常只需确认目标库已启用 `vector` 且支持 `vector(1024)`。如需升级 pgvector 本身，先停服并备份，由管理员在数据库服务器上更新与 PostgreSQL 主版本匹配的扩展文件，再连接到每个需要升级的数据库执行：

```sql
ALTER EXTENSION vector UPDATE;
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

`--apply` 只负责首次启用扩展，对已启用的扩展不会更新版本。更新后用 `--check` 复验目标库，再继续应用 schema 升级和服务启动。托管数据库由平台提供扩展文件时，按其扩展升级流程处理。

## 4. 脚本做了什么

| 阶段 | 行为 |
|---|---|
| 解析目标库 | `--db` > 环境变量 `DATABASE_URL` > `MELONCLAW_ENV_FILE` 指向的配置文件 > 项目根 `.env`。只提取库名 |
| `--check` | 只读：PG 版本、`pg_config` 目录、扩展文件是否存在、目标库能否连接、当前角色是否超级用户、扩展版本、`vector` 类型与 `vector(1024)` 临时表验证 |
| `--apply` | 唯一数据库变更：`CREATE EXTENSION IF NOT EXISTS vector;`；随后复验扩展版本、`vector` 类型、`vector(1024)` |
| `--install` | 仅 `--apply` 且仅 macOS/Homebrew 时执行 `brew install pgvector`；Linux 只打印管理员命令 |
| `--admin-user` | 指定执行 `CREATE EXTENSION` 的数据库角色 |

**脚本不做**：建库、删库、改已有库数据、输出密码或连接串、读取配置文件除库名以外的内容、执行 `melonclaw-db-init`／`melonclaw-db-update`、备份、停服、建业务表。`--check` 永远只读；变更必须显式 `--apply`。

失败时脚本会停止并打印原因，未删除任何数据；已完成的幂等变更可以直接重跑。

## 5. 常见失败与处理

| 现象 | 处理 |
|---|---|
| 「扩展文件未就位」 | 按第 2.3 节安装；确认 `pg_config --pkglibdir` 与 `psql --version` 指向同一个 PG 主版本 |
| 「无法连接目标库」 | 确认库已创建、服务已启动；跨主机设置 `PGHOST`／`PGPORT`／`PGUSER`，认证用 `.pgpass` 或已导出的 `PGPASSWORD` |
| 「CREATE EXTENSION 失败」 | 当前角色权限不足。让管理员执行，或用 `--admin-user` 指定管理员角色；不要给应用账号扩大长期权限 |
| 「未能解析目标库名」 | 检查 `MELONCLAW_ENV_FILE` 与 `.env` 中的 `DATABASE_URL`；也可用 `--db` 显式指定 |
| 装完仍找不到扩展文件 | Homebrew 预编译包可能对应另一个 PG 版本，按第 2.3 节用当前 `pg_config` 源码编译 |

分享日志前删除或脱敏凭据。不要公开连接串或提交 `.env`。
