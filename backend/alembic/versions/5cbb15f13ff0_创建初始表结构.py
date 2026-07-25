"""创建初始表结构

Revision ID: 5cbb15f13ff0
Revises:
Create Date: 2026-07-25 12:17:17.283522

⚠️ 本文件在 autogenerate 生成后做了**手工修改**，改动如下（见 docs/07-bug-log.md BUG-004）：

    1. practice_mode ENUM 提取为模块级对象，带 create_type=False
       —— 否则两张表各自 create_table 时会重复 CREATE TYPE，第二次报
          DuplicateObjectError
    2. upgrade() 开头显式 create(checkfirst=True) 建一次类型
    3. downgrade() 末尾 drop() 删掉类型
       —— autogenerate 不会生成这行，导致 downgrade 后 ENUM 残留，
          再 upgrade 就失败

以后新增用到 ENUM 的表，记得检查同样的问题。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5cbb15f13ff0'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# create_type=False：让 create_table 不要自己发 CREATE TYPE，
# 由 upgrade() 开头统一建一次。
practice_mode = postgresql.ENUM(
    'dictation', 'recognition', name='practice_mode', create_type=False
)


def upgrade() -> None:
    """Upgrade schema."""
    # 手工添加：显式建 ENUM 类型，只建一次
    practice_mode.create(op.get_bind(), checkfirst=True)

    op.create_table('users',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('username', sa.String(length=50), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('nickname', sa.String(length=50), nullable=False),
    sa.Column('wx_openid', sa.String(length=64), nullable=True),
    sa.Column('wx_unionid', sa.String(length=64), nullable=True),
    sa.Column('daily_new_limit', sa.Integer(), nullable=False),
    sa.Column('daily_review_limit', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('wx_openid'),
    sa.UniqueConstraint('wx_unionid')
    )
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_table('word_lists',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('owner_id', sa.UUID(), nullable=True),
    sa.Column('is_public', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('words',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('word', sa.String(length=100), nullable=False),
    sa.Column('meaning', sa.Text(), nullable=False),
    sa.Column('meaning_primary', sa.Text(), nullable=False),
    sa.Column('phonetic', sa.String(length=100), nullable=True),
    sa.Column('part_of_speech', sa.String(length=20), nullable=True),
    sa.Column('topic', sa.String(length=50), nullable=True),
    sa.Column('audio_url', sa.String(length=500), nullable=True),
    sa.Column('audio_source', sa.String(length=20), nullable=True),
    sa.Column('exam_tags', sa.String(length=100), nullable=True),
    sa.Column('bnc', sa.Integer(), nullable=True),
    sa.Column('frq', sa.Integer(), nullable=True),
    sa.Column('difficulty', sa.SmallInteger(), nullable=False),
    sa.Column('word_list_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['word_list_id'], ['word_lists.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('word_list_id', 'word', name='uq_words_list_word')
    )
    op.create_index('idx_words_frq', 'words', ['frq'], unique=False)
    op.create_index('idx_words_list', 'words', ['word_list_id'], unique=False)
    op.create_index('idx_words_topic', 'words', ['topic'], unique=False)
    op.create_table('answer_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('word_id', sa.UUID(), nullable=False),
    sa.Column('mode', practice_mode, nullable=False),
    sa.Column('is_correct', sa.Boolean(), nullable=False),
    sa.Column('user_input', sa.Text(), nullable=True),
    sa.Column('answered_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('device_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['word_id'], ['words.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_events_replay', 'answer_events', ['user_id', 'word_id', 'mode', 'answered_at'], unique=False)
    op.create_index('idx_events_user_time', 'answer_events', ['user_id', 'answered_at'], unique=False)
    op.create_table('user_progress',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('word_id', sa.UUID(), nullable=False),
    sa.Column('mode', practice_mode, nullable=False),
    sa.Column('box', sa.SmallInteger(), nullable=False),
    sa.Column('next_review_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('correct_count', sa.Integer(), nullable=False),
    sa.Column('wrong_count', sa.Integer(), nullable=False),
    sa.Column('last_answered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('box BETWEEN 1 AND 5', name='ck_progress_box_range'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['word_id'], ['words.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'word_id', 'mode')
    )
    op.create_index('idx_progress_due', 'user_progress', ['user_id', 'mode', 'next_review_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_progress_due', table_name='user_progress')
    op.drop_table('user_progress')
    op.drop_index('idx_events_user_time', table_name='answer_events')
    op.drop_index('idx_events_replay', table_name='answer_events')
    op.drop_table('answer_events')
    op.drop_index('idx_words_topic', table_name='words')
    op.drop_index('idx_words_list', table_name='words')
    op.drop_index('idx_words_frq', table_name='words')
    op.drop_table('words')
    op.drop_table('word_lists')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_table('users')

    # 手工添加：autogenerate 不会生成这行，导致 ENUM 类型残留
    practice_mode.drop(op.get_bind(), checkfirst=True)
