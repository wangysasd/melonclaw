# PostgreSQL 准备指南

准备 PostgreSQL 14+ 和 `psql` 客户端。启动数据库服务。
以下示例使用本机端口 `5432`。远程数据库请替换地址和端口。

## 创建账号和数据库

以数据库管理员身份打开 `psql`。Linux 常用命令：

```bash
sudo -u postgres psql
```

其他安装方式请使用安装时设置的管理员账号。进入后执行：

```sql
CREATE ROLE melonclaw_app LOGIN;
\password melonclaw_app
CREATE DATABASE melonclaw OWNER melonclaw_app;
\q
```

按提示设置密码。已有账号或数据库时，先检查是否可用，不要删除重建。

## 验证连接

在终端执行，按提示输入密码：

```bash
psql -h 127.0.0.1 -p 5432 -U melonclaw_app -d melonclaw -W
```

连接后验证建表权限：

```sql
BEGIN;
CREATE TABLE public.melonclaw_connection_check (id integer);
ROLLBACK;
\q
```

看到 `CREATE TABLE` 和 `ROLLBACK` 即验证通过。测试表不会保留。

## 配置应用

返回 [README](../README.md#配置文件)，填写 `.env` 并初始化应用。
使用刚才验证过的连接信息。密码中的特殊字符需进行 URL 百分号编码。
不要公开连接串或提交 `.env`。

连接失败时，检查服务、地址、端口和密码。建表失败时，请管理员检查数据库归属和 `public` schema 权限。
