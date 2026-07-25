# 📦 pnpm 是什么？

> 记录时间：2026-07-25

## 一句话

**pnpm** = Performant npm — 一个比 npm 更快、更省磁盘的 JavaScript 包管理工具。

## 为什么会有 pnpm？

以前有 npm 和 yarn 两个包管理工具，它们都有一个问题：

```
项目 A 用 lodash  → 下载一份到 node_modules
项目 B 用 lodash  → 又下载一份到 node_modules
```

每个项目都**重复下载**同样的包，浪费磁盘空间。

## pnpm 的做法

pnpm 用**全局存储 + 硬链接**的方式解决：

```
全局仓库（~/.pnpm-store）
  └── lodash@4.0.0  ← 只存一份

项目 A node_modules/
  └── lodash  →  硬链接指向全局仓库

项目 B node_modules/
  └── lodash  →  硬链接指向同一份
```

- **省空间**：100 个项目用 lodash，也只存一份
- **省时间**：装过的包秒级复用
- **更安全**：node_modules 结构更严格，不会出现"幽灵依赖"

## 和 npm / yarn 的对比

| 特性 | npm | yarn | pnpm |
|------|-----|------|------|
| 磁盘占用 | ❌ 每个项目重复下载 | ❌ 同 npm | ✅ 硬链接复用 |
| 安装速度 | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Monorepo 支持 | ⭐⭐（需 workspace 插件） | ⭐⭐⭐ | ⭐⭐⭐⭐⭐（原生） |
| 严格性 | ⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ |

## 本项目中 pnpm 的用法

```bash
pnpm install           # 安装所有依赖
pnpm add react         # 安装依赖到当前 workspace
pnpm -F @ielts/frontend add axios   # 安装到指定子项目
pnpm dev               # 执行根目录的 dev 脚本
```

## 关键概念：workspace

pnpm 内置 workspace 功能，让你在一个仓库里管理多个子项目。通过在 `pnpm-workspace.yaml` 中声明哪些目录是"子项目"来工作：

```yaml
packages:
  - "apps/*"       # apps/ 下面的所有都是子项目
  - "packages/*"   # packages/ 下面的所有都是子项目
  - "backend"      # backend/ 也是一个子项目
```

---

**相关笔记**：[[02-what-is-monorepo]]
