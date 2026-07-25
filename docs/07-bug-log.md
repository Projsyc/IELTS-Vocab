# 07 BUG 与修复日志

> 倒序排列，最新的在最上面。
> **用法**：遇到问题先 `Ctrl+F` 搜这里，可能已经踩过。

---

## 模板

```markdown
## BUG-XXX 一句话标题

| | |
|---|---|
| **日期** | YYYY-MM-DD |
| **严重度** | 🔴 阻塞 / 🟡 影响功能 / 🟢 小问题 |
| **状态** | ✅ 已修复 / 🔧 临时绕过 / ❌ 未解决 |

**现象**

**根因**

**解决方案**

**如何避免再犯**
```

---

## BUG-008 async engine 与事件循环 —— 同一根因踩了三次

| | |
|---|---|
| **日期** | 2026-07-25 |
| **严重度** | 🟡 影响测试与脚本 |
| **状态** | ✅ 已修复，并整理成通用规则 |

**这不是一个新 BUG，而是 BUG-005 / BUG-006 的第三次复发。** 单独列出来是因为
同一根因换三个地方咬人，说明需要一条能记住的规则，而不是三条各自的修复。

### 根因（一句话）

**async engine 的连接池绑定在创建它的事件循环上。**
pytest-asyncio 默认每个测试一个新循环，所以任何跨循环复用的 engine
在第二次使用时必然报 `RuntimeError: Event loop is closed`。

### 三次现场

| # | 场景 | 错误的写法 |
|---|------|-----------|
| BUG-005 | 单测里查表 | 测试里直接 import 模块级 `engine` |
| BUG-006 | 脚本收尾 | `finally: asyncio.run(engine.dispose())` —— 用第二个循环关第一个循环的连接 |
| 本条 | HTTP 测试 | 打 `main.app`，而 app 里的路由通过 `get_db` 用模块级 engine |

第三次的表现很有迷惑性：**前 19 个测试过，4 个挂**。
挂的是那些在同一测试里发起多次请求、或请求路径更长的 —— 看着像随机失败。

### 三条规则（已写进 `tests/conftest.py` 文件头）

```
1. 测试里的 engine 必须是 fixture，每个测试新建并 dispose
2. dispose 必须 await 在建立连接的那个循环里，
   永远不要用第二个 asyncio.run() 清理第一个留下的资源
3. HTTP 测试必须覆盖 get_db 依赖，否则 app 会用模块级 engine
```

第 3 条的写法：

```python
@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session

    main.app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=main.app), ...) as c:
            yield c
    finally:
        main.app.dependency_overrides.clear()
```

顺带的好处：app 与测试共用一个 session，测试里创建的数据对被测接口立即可见，
不用操心事务隔离。

### 为什么生产代码不受影响

生产环境整个进程只有一个事件循环（uvicorn 建的），模块级 engine 完全正常。
**这个坑只在"一个进程里存在多个事件循环"时出现** —— 也就是测试和 CLI 脚本。

---

## BUG-007 passlib 读不了 bcrypt 5.0 的版本号

| | |
|---|---|
| **日期** | 2026-07-25 |
| **严重度** | 🔴 阻塞 — 密码哈希完全不可用 |
| **状态** | ✅ 已修复（改为直接用 bcrypt，移除 passlib） |

**现象**

按 `requirements.txt` 里原定的 `passlib[bcrypt]` 写密码哈希，一跑就炸：

```
(trapped) error reading bcrypt version
AttributeError: module 'bcrypt' has no attribute '__about__'
ValueError: password cannot be longer than 72 bytes, truncate manually if necessary
```

**根因**

passlib 1.7.4 靠读 `bcrypt.__about__.__version__` 探测后端版本，
但 **bcrypt 4.1+ 已移除该属性**（我们装的是 5.0.0）。
版本探测失败后 passlib 走了一条降级路径，那条路径也是坏的。

passlib 最后一次发版是 **2020 年**，实际已停止维护，不会有适配。

**解决方案**

移除 passlib，直接用 `bcrypt` 库 —— API 只有两个函数：

```python
bcrypt.hashpw(pw_bytes, bcrypt.gensalt())   # 哈希
bcrypt.checkpw(pw_bytes, stored)            # 校验
```

**但要处理 bcrypt 的 72 字节上限**（超了直接抛 ValueError，且遇 NUL 字节会截断）。
方案是 SHA-256 预哈希：

```python
def _prehash(password: str) -> bytes:
    return base64.b64encode(hashlib.sha256(password.encode()).digest())   # 恒定 44 字节
```

这同时解决长度上限和 NUL 截断两个问题，是业界常见做法。

⚠️ **预哈希方式一旦上线就不能改** —— 改了所有已存密码失效。
真要改需加 `users.hash_version` 字段做迁移。已在 `core/security.py` 模块 docstring 写明。

**如何避免再犯**

- 选依赖时先看**最后发版时间**。passlib 停更 6 年，本该是个信号
- 装完立刻写个最小验证脚本跑一遍，别等写完整个模块才发现底层不可用
- 已写测试守护关键性质：超长密码可用、72 字节之后的差异能被区分、NUL 字节不截断

---

## BUG-006 seed 脚本收尾报 "Event loop is closed"（BUG-005 的同类错误）

| | |
|---|---|
| **日期** | 2026-07-25 |
| **严重度** | 🟢 小问题 —— 数据已正确写入，只是退出时报错 |
| **状态** | ✅ 已修复 |

**现象**

`seed_words.py` 跑完，数据全对，但退出时刷一大堆栈：

```
RuntimeError: Task ... got Future ... attached to a different loop
RuntimeError: Event loop is closed
```

**根因**

我写了这样的收尾：

```python
def main():
    try:
        return asyncio.run(main_async(args))    # ← 循环 1，engine 的连接在这里建立
    finally:
        asyncio.run(engine.dispose())           # ← 循环 2，去关循环 1 的连接 → 炸
```

**和 BUG-005 是同一类错误** —— async engine 的连接池绑定在创建它的事件循环上。
刚在测试里踩过一次，写脚本时又踩了，说明这个模式很容易复现。

**解决方案**

在同一个循环内销毁：

```python
async def main_async(args):
    try:
        ...
    finally:
        await engine.dispose()      # ← 本循环内

def main():
    return asyncio.run(main_async(args))
```

**如何避免再犯**

**记住这条规则**：`engine.dispose()` 必须 `await` 在建立连接的那个循环里，
永远不要用第二个 `asyncio.run()` 去清理第一个 `asyncio.run()` 留下的资源。

---

## BUG-005 pytest 里第二个 async 测试报 "Event loop is closed"

| | |
|---|---|
| **日期** | 2026-07-25 |
| **严重度** | 🟡 影响测试 |
| **状态** | ✅ 已修复 |

**现象**

7 个结构测试里 3 个失败：

```
RuntimeError: Event loop is closed
RuntimeWarning: coroutine 'Connection._cancel' was never awaited
```

诡异的是**前几个测试能过，后面的挂** —— 说明不是逻辑错，是状态污染。

**根因**

最初的写法是每个测试函数里调 `asyncio.run()`，但用的是**模块级的 engine**：

```python
from app.core.database import engine   # ← 模块级，只有一个

def test_a():
    asyncio.run(_query())   # 创建循环 1，engine 连接池绑定到循环 1，结束后循环 1 关闭
def test_b():
    asyncio.run(_query())   # 创建循环 2，但连接池里的连接还指着已关闭的循环 1 → 炸
```

async engine 的**连接池绑定在创建它的事件循环上**，跨循环复用必挂。

**解决方案**

1. 用 `pytest-asyncio`（`asyncio_mode = auto`），别手写 `asyncio.run()`
2. `pytest.ini` 里设 `asyncio_default_fixture_loop_scope = function`
3. **engine 改成 fixture，每个测试新建并 `dispose()`**（见 `tests/conftest.py`）

```python
@pytest_asyncio.fixture
async def db_conn():
    engine = create_async_engine(settings.DATABASE_URL)  # 每个测试自己的
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()
```

**如何避免再犯**

写 async 数据库测试时，engine 必须是 fixture 而非模块级单例。
生产代码里模块级 engine 没问题（整个进程一个事件循环），只有测试才有多循环问题。

---

## BUG-004 Alembic downgrade 后 ENUM 类型残留，导致无法重新 upgrade

| | |
|---|---|
| **日期** | 2026-07-25 |
| **严重度** | 🔴 阻塞 — 迁移不可逆 |
| **状态** | ✅ 已修复 |

**现象**

`alembic downgrade base` 成功，表都删了。但紧接着 `alembic upgrade head` 报错：

```
asyncpg.exceptions.DuplicateObjectError: type "practice_mode" already exists
[SQL: CREATE TYPE practice_mode AS ENUM ('dictation', 'recognition')]
```

**根因**

两个独立的 Alembic 缺陷叠在一起：

1. **`downgrade()` 不删 ENUM 类型** —— autogenerate 只生成 `drop_table`，
   不会生成对应的 `DROP TYPE`。表删了，类型留着。

   验证：
   ```console
   $ alembic downgrade base
   $ psql -tAc "select typname from pg_type where typname='practice_mode'"
   practice_mode          ← 还在
   ```

2. **同一个 ENUM 被两张表引用时会重复 CREATE** —— `answer_events` 和
   `user_progress` 都有 `mode` 列，两次 `create_table` 各自尝试建类型。
   （这次首轮 upgrade 侥幸没炸，但设计上就是错的。）

**解决方案**

手工改迁移脚本三处：

```python
# 1. 提取为模块级对象，create_type=False 让 create_table 别自己建
practice_mode = postgresql.ENUM(
    'dictation', 'recognition', name='practice_mode', create_type=False
)

def upgrade():
    practice_mode.create(op.get_bind(), checkfirst=True)   # 2. 显式建一次
    ...
    sa.Column('mode', practice_mode, nullable=False),      # 复用同一对象
    ...

def downgrade():
    ...
    practice_mode.drop(op.get_bind(), checkfirst=True)     # 3. 显式删掉
```

**验证方式**

往返测三遍，确认可逆：

```console
$ alembic upgrade head && alembic downgrade base && alembic upgrade head
残留 ENUM: 0
✅ 三轮全部成功
```

**如何避免再犯**

- **每写完一个迁移，必须测 `upgrade → downgrade → upgrade` 往返**，
  只测 upgrade 会漏掉这类问题
- 以后新增用到 ENUM 的表，检查同样的两点
- autogenerate 的产物是**草稿**，不是成品 —— 提交前必须读一遍

---

## BUG-003 SQLAlchemy async 报 "the greenlet library is required"

| | |
|---|---|
| **日期** | 2026-07-25 |
| **严重度** | 🔴 阻塞 — Alembic 完全跑不了 |
| **状态** | ✅ 已修复 |

**现象**

`alembic revision --autogenerate` 报错：

```
ValueError: the greenlet library is required to use this function.
No module named 'greenlet'
```

**根因**

SQLAlchemy 的 async 支持（`create_async_engine`）底层用 `greenlet` 在同步/异步之间桥接，但 **`greenlet` 不是 `sqlalchemy` 的硬依赖** —— 只有装 `sqlalchemy[asyncio]` 这个 extra 才会被拉进来。

`requirements.txt` 里写的是裸 `sqlalchemy>=2.0.0`，所以没装上。

**解决方案**

在 `requirements.txt` 显式声明：

```
sqlalchemy>=2.0.0
greenlet>=3.0.0          # SQLAlchemy async 的隐式依赖
```

**如何避免再犯**

用 async ORM 时，要么写 `sqlalchemy[asyncio]`，要么显式列出 `greenlet`。
本项目选后者 —— 依赖显式列出更容易看懂，不用去查某个 extra 里到底装了什么。

---

## BUG-002 Docker Desktop 启动失败：Docker.raw 属主是 root

| | |
|---|---|
| **日期** | 2026-07-25 |
| **严重度** | 🔴 阻塞 — 数据库跑不起来 |
| **状态** | ✅ 已修复 |

**现象**

Docker Desktop 启动时弹窗报错并退出：

```
running engine: waiting for the VM setup to be ready: preparing VM: ensuring disk:
Cannot resize ".../vms/0/data/Docker.raw" to 75443MiB:
truncate .../Docker.raw: permission denied
```

**根因**

VM 磁盘镜像文件的属主是 `root`，而 Docker Desktop 以普通用户身份运行，改不动它：

```console
$ ls -lh ~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw
-rw-r--r--  1 root  staff  73G  Docker.raw
              ↑↑↑↑ 应该是当前用户
```

磁盘空间充足（1.5Ti 可用），排除了空间不足这个更常见的原因。
推测是之前某次操作用了 `sudo`，导致文件属主被改成 root。

**解决方案**

```bash
sudo chown "$(id -un):staff" \
  ~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw
```

然后重启 Docker Desktop。

**如何避免再犯**

不要用 `sudo` 运行 Docker Desktop 或操作它的数据目录。
`docker` CLI 命令本身也不需要 sudo（macOS 上 Docker Desktop 已处理好权限）。

---

## BUG-001 Python 3.14 beta 导致 FastAPI 无法导入

| | |
|---|---|
| **日期** | 2026-07-25 |
| **严重度** | 🔴 阻塞 — 后端完全跑不起来 |
| **状态** | ✅ 已修复 |

**现象**

创建 venv 装好依赖后，`import main` 直接崩：

```
File ".../pydantic/_internal/_typing_extra.py", line 474, in eval_type_backport
    return _eval_type_backport(value, globalns, localns, type_params)
TypeError: _eval_type() got an unexpected keyword argument 'prefer_fwd_module'

During handling of the above exception, another exception occurred:
    assert isinstance(value, typing.ForwardRef)
AssertionError
```

**根因**

系统默认 Python 被 pyenv 设为 **`3.14.0b3`（beta 版）**：

```bash
$ pyenv versions
  system
  3.9.18
* 3.14.0b3 (set by /Users/acccan/.python-version)
```

Python 3.14 改了 `typing._eval_type()` 的签名（新增 `prefer_fwd_module` 参数），而当前版本的 pydantic 还没适配。FastAPI 内部大量依赖 pydantic 做类型解析，于是导入阶段就炸了。

**解决方案**

用 Homebrew 的 Python 3.13 重建 venv：

```bash
rm -rf backend/.venv
/opt/homebrew/bin/python3.13 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
```

验证：

```bash
$ cd backend && .venv/bin/python -c "import main; print('OK:', main.app.title)"
OK: IELTS Vocabulary API
```

**如何避免再犯**

1. 在 `backend/.python-version` 写入 `3.13`，锁定版本
2. `docs/02-architecture.md` 环境要求里明确标注"不能用 3.14 beta"
3. **通用教训**：生产项目不要用 beta 版语言运行时。第三方库的适配总是滞后于语言发布

---
