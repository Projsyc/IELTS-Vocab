"""词库与单词表。

字段来源与实测覆盖率见 docs/09-wordlist-research.md
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class WordList(Base):
    __tablename__ = "word_lists"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # NULL = 系统内置词库。v2 做用户上传时指向上传者（见 ADR-006）
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    words: Mapped[list["Word"]] = relationship(
        back_populates="word_list", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<WordList {self.name}>"


class Word(Base):
    __tablename__ = "words"
    __table_args__ = (
        UniqueConstraint("word_list_id", "word", name="uq_words_list_word"),
        Index("idx_words_topic", "topic"),          # 干扰项按话题抽
        Index("idx_words_list", "word_list_id"),
        Index("idx_words_frq", "frq"),              # 按词频选词 / 难度分级
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    word: Mapped[str] = mapped_column(String(100), nullable=False)

    # 释义双字段（一词多义占 75.1%，见 ADR-008）
    #   meaning         完整释义，多义项用 " / " 分隔，答题后展示
    #   meaning_primary 第一义项，阅读模式 4 选 1 用
    # 分开存的关键理由：完整释义长度 11–169 字，长度差异本身会泄露答案；
    # 首义长度均匀（中位数 14 字），消除这个信号。
    meaning: Mapped[str] = mapped_column(Text, nullable=False)
    meaning_primary: Mapped[str] = mapped_column(Text, nullable=False)

    phonetic: Mapped[str | None] = mapped_column(String(100))       # IPA，如 /əˈkɒmədeɪt/
    part_of_speech: Mapped[str | None] = mapped_column(String(20))  # 从释义前缀解析，99.5%

    # 阅读模式干扰项的命根子。无现成数据源，由 LLM 批量打标。
    topic: Mapped[str | None] = mapped_column(String(50))

    # seed 阶段全部本地化，正常情况 100% 有值（热链实测 760ms 不可用，见 ADR-009）
    audio_url: Mapped[str | None] = mapped_column(String(500))
    audio_source: Mapped[str | None] = mapped_column(String(20))  # 'dictapi' | 'edge-tts'

    # 来自 ECDICT 的元数据
    exam_tags: Mapped[str | None] = mapped_column(String(100))  # "cet6 toefl ielts gre"
    bnc: Mapped[int | None] = mapped_column(Integer)            # BNC 语料词频排名
    frq: Mapped[int | None] = mapped_column(Integer)            # 当代语料词频排名

    difficulty: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)

    word_list_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("word_lists.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    word_list: Mapped[WordList] = relationship(back_populates="words")

    def __repr__(self) -> str:
        return f"<Word {self.word}>"
