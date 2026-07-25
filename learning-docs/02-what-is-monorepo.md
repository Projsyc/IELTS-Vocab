# 🏗️ Monorepo（单体仓库）是什么？

> 记录时间：2026-07-25

## 一句话

**Monorepo** = 把前端、后端、移动端等**所有代码放在同一个 Git 仓库**中管理。

## 对比理解

### 传统方式（MultiRepo — 多仓库）

```
github/
├── IELTS-Vocab-Frontend/   ← 一个仓库
├── IELTS-Vocab-Backend/    ← 另一个仓库
└── IELTS-Vocab-Android/    ← 又一个仓库
```

**问题**：
- 前端定义的类型（比如 `User` 接口），后端要复制一份
- 改个字段名要跑 N 个仓库去改
- 跨仓库的 Issue / PR 管理麻烦

### Monorepo 方式

```
IELTS-Vocab/                ← 只有一个仓库
├── apps/frontend/          # React 前端
├── backend/                # FastAPI 后端
├── apps/mobile/            # Android（未来）
└── packages/shared/        # 共享代码！
```

**好处**：
- `packages/shared/` 里定义一次类型，前端后端都能引用
- 一个 `git commit` 可以原子化修改前后端
- 统一的 Issue / CI / 发布流程

## 不是"把代码堆到一起"

Monorepo 不是大锅饭。每个子项目（workspace）**依然独立**：
- 有自己的 `package.json`
- 可以单独构建、测试
- 可以只安装自己的依赖

```bash
cd apps/frontend && pnpm dev    # 只启动前端
cd backend && python main.py    # 只启动后端
pnpm -F @ielts/frontend add axios  # 只给前端加依赖
```

## 需要工具配合

要发挥 Monorepo 的真正威力，需要包管理工具支持：

- **pnpm workspace**：基础，声明哪些目录是子项目，让子项目之间可以互相引用
- **Turborepo**（可选进阶）：在 pnpm 基础上加缓存，只构建改动的项目

详见 [[01-what-is-pnpm]]。

## 本项目的 Monorepo 结构

```
IELTS-Vocab/
├── pnpm-workspace.yaml    ← 声明子项目范围
├── package.json           ← 根配置（不发布）
├── apps/
│   ├── frontend/          ← 前端项目（package.json 独立）
│   └── mobile/            ← 未来 Android 项目
├── backend/               ← 后端项目（Python，不走 pnpm）
├── packages/
│   └── shared/            ← 共享包（TypeScript 类型、工具函数）
└── docs/                  ← 项目文档
```

> **💡 注意**：后端是 Python（FastAPI），不在 pnpm 管理范围内。pnpm workspace 只管理 JS/TS 项目。但它们在同一个 git 仓库里，仍然享受 Monorepo 的好处。

---

**相关笔记**：[[01-what-is-pnpm]]
