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
