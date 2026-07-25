// ============================================================
// @ielts/shared — 前后端共享的类型定义
//
// ⚠️ 后端是 Python，吃不到这些类型，两边需人工保持一致。
//    对应的表结构见 docs/03-data-model.md
//    v2 考虑：从 FastAPI 的 OpenAPI schema 自动生成，消除不一致
// ============================================================

// ----- 枚举 -----

/** 练习模式。听写和阅读的进度完全独立（见 ADR-003） */
export type PracticeMode = 'dictation' | 'recognition'

/** Leitner 盒子号，1–5 */
export type LeitnerBox = 1 | 2 | 3 | 4 | 5

/** Leitner 复习间隔（天），与后端配置保持一致 */
export const BOX_INTERVALS: Record<LeitnerBox, number> = {
  1: 1,
  2: 2,
  3: 4,
  4: 7,
  5: 15,
}

// ----- 用户 -----

export interface User {
  id: string
  username: string
  nickname: string
  dailyNewLimit: number
  dailyReviewLimit: number
  createdAt: string
}

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  accessToken: string
  tokenType: 'bearer'
  user: User
}

// ----- 单词 -----

export interface Word {
  id: string
  word: string
  meaning: string
  phonetic: string | null
  partOfSpeech: string | null
  /** 雅思话题分类 —— 阅读模式干扰项按此抽取 */
  topic: string | null
  /** 预生成音频路径。null 表示前端需降级用浏览器 TTS */
  audioUrl: string | null
  difficulty: 1 | 2 | 3
}

export interface WordList {
  id: string
  name: string
  description: string | null
  wordCount: number
  isPublic: boolean
}

export interface WordListStats {
  wordListId: string
  total: number
  dictation: MasteryBreakdown
  recognition: MasteryBreakdown
}

export interface MasteryBreakdown {
  /** 无进度记录 */
  new: number
  /** box 1–4 */
  learning: number
  /** box 5 */
  mastered: number
}

// ----- 练习 -----

/** 阅读模式的一个选项。正确答案不下发，由后端判定 */
export interface ChoiceOption {
  index: 1 | 2 | 3 | 4
  text: string
}

export interface PracticeItem {
  wordId: string
  /** ⚠️ 听写模式下前端不得展示，仅用于提交后比对 */
  word: string
  phonetic: string | null
  meaning: string | null
  audioUrl: string | null
  /** 当前盒子号，null 表示新词 */
  box: LeitnerBox | null
  /** 仅阅读模式有值，已打乱顺序 */
  options: ChoiceOption[] | null
}

export interface PracticeSession {
  mode: PracticeMode
  total: number
  reviewCount: number
  newCount: number
  items: PracticeItem[]
}

export type SessionScope = 'all' | 'review_only' | 'new_only' | 'topic'

export interface CreateSessionRequest {
  listId: string
  mode: PracticeMode
  count: number
  scope: SessionScope
  /** scope === 'topic' 时必填 */
  topic?: string
}

// ----- 答题 -----

export interface AnswerRequest {
  wordId: string
  mode: PracticeMode
  /** 听写：用户拼写内容；阅读：选项 index 的字符串，或 'unknown' */
  userInput: string
  /** ⭐ 客户端答题时刻（ISO 8601）。回放按此排序，不是入库时间 */
  answeredAt: string
  deviceId: string
}

/** 听写模式的逐字符比对结果，用于高亮错误位置 */
export interface DiffChar {
  pos: number
  char: string
  status: 'ok' | 'wrong' | 'missing' | 'extra'
  /** status 为 wrong / missing 时给出正确字符 */
  expected?: string
}

export interface AnswerResponse {
  isCorrect: boolean
  correctAnswer: string
  /** 仅听写模式返回 */
  diff: DiffChar[] | null
  progress: ProgressState
}

// ----- 进度 -----

export interface ProgressState {
  box: LeitnerBox
  nextReviewAt: string
}

export interface ProgressSummary {
  streakDays: number
  today: {
    answered: number
    correct: number
    accuracy: number
  }
  boxes: Record<PracticeMode, Record<LeitnerBox, number>>
  dueToday: Record<PracticeMode, number>
}

export interface WrongWordEntry {
  word: Word
  wrongCount: number
  lastWrongAt: string
  /** 最近几次的错误拼写，看看老是怎么拼错的 */
  recentInputs: string[]
}
