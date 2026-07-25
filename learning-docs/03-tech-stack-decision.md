# 🧰 技术选型决策记录

> 记录时间：2026-07-25

## 最终决策

| 层 | 选型 | 理由 |
|----|------|------|
| 前端框架 | **React + Vite + TypeScript** | 生态丰富，社区活跃，适合复杂交互 |
| 后端框架 | **Python FastAPI** | 开发效率高，天然支持异步，API 文档自动生成 |
| 数据库 | **PostgreSQL** | 功能强大，支持 JSON，适合用户和进度数据 |
| Monorepo 工具 | **pnpm workspace** | 零配置够用，以后可无缝升级 Turborepo |
| 包管理 | **pnpm** | 省磁盘、快、Monorepo 支持最好 |

## 为什么不选其他方案

### 前端：Vue？
Vue 本身很好，但 React 生态更庞大，且万一以后要做 React Native 移动端可以复用知识。

### 后端：Node.js (NestJS)？
JS/TS 全栈统一语言确实诱人，但考虑到后续可能需要 NLP 处理（单词分析、语音识别等），Python 生态（NLTK、spaCy、语音库）更合适。

### 后端：Go？
性能强但开发速度不如 Python，对新手不友好。

### Monorepo：Nx？
太重了，适合大型企业项目，本项目当前不需要。

---

**相关笔记**：[[01-what-is-pnpm]], [[02-what-is-monorepo]]
