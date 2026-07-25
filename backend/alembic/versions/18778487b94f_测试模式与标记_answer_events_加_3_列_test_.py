"""测试模式与标记：answer_events 加 3 列 + test_sessions + word_stars

Revision ID: 18778487b94f
Revises: 5cbb15f13ff0
Create Date: 2026-07-25 15:40:01.293786

⚠️ 本文件在 autogenerate 生成后做了**三处手工修改**：

1. `practice_mode` ENUM 复用已存在的类型（`create_type=False`）
   —— 否则 test_sessions 的 create_table 会再发一次 CREATE TYPE，
      报 DuplicateObjectError（同 BUG-004）

2. `is_test` 加 `server_default='false'`
   —— 表里已有 10 行数据，给已有行加 NOT NULL 列却没默认值会直接失败。
      autogenerate 不知道表里有数据，生成的是不能跑的 SQL。

3. downgrade 里的 `drop_constraint(None, ...)` 补上真实约束名
   —— autogenerate 生成的是 None，跑起来会挂。
      顺便给两个外键起了显式名字，方便回滚时引用。

教训写进 docs/07-bug-log.md BUG-009：
**autogenerate 不知道表里有没有数据，加非空列时必须自己补默认值。**
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '18778487b94f'
down_revision: Union[str, Sequence[str], None] = '5cbb15f13ff0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 手工修改 1：复用已存在的 ENUM 类型，不要重复创建
practice_mode = postgresql.ENUM(
    'dictation', 'recognition', name='practice_mode', create_type=False
)

FK_CORRECTS = "fk_answer_events_corrects_event_id"
FK_TEST_SESSION = "fk_answer_events_test_session_id"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'test_sessions',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('mode', practice_mode, nullable=False),
        sa.Column('scope', sa.String(length=20), nullable=False),
        sa.Column('scope_value', sa.String(length=50), nullable=True),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_test_sessions_user_time', 'test_sessions', ['user_id', 'created_at'], unique=False)

    op.create_table(
        'word_stars',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('word_id', sa.UUID(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['word_id'], ['words.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'word_id'),
    )
    op.create_index('idx_word_stars_user', 'word_stars', ['user_id', 'created_at'], unique=False)

    # 手工修改 2：server_default 必须有 —— 表里已有数据
    op.add_column(
        'answer_events',
        sa.Column('is_test', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column('answer_events', sa.Column('test_session_id', sa.UUID(), nullable=True))
    op.add_column('answer_events', sa.Column('corrects_event_id', sa.BigInteger(), nullable=True))

    op.create_index('idx_events_test_session', 'answer_events', ['test_session_id'], unique=False)

    # 手工修改 3：给外键显式命名，否则 downgrade 无法引用
    op.create_foreign_key(
        FK_CORRECTS, 'answer_events', 'answer_events',
        ['corrects_event_id'], ['id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        FK_TEST_SESSION, 'answer_events', 'test_sessions',
        ['test_session_id'], ['id'], ondelete='CASCADE',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(FK_TEST_SESSION, 'answer_events', type_='foreignkey')
    op.drop_constraint(FK_CORRECTS, 'answer_events', type_='foreignkey')
    op.drop_index('idx_events_test_session', table_name='answer_events')
    op.drop_column('answer_events', 'corrects_event_id')
    op.drop_column('answer_events', 'test_session_id')
    op.drop_column('answer_events', 'is_test')

    op.drop_index('idx_word_stars_user', table_name='word_stars')
    op.drop_table('word_stars')
    op.drop_index('idx_test_sessions_user_time', table_name='test_sessions')
    op.drop_table('test_sessions')

    # ENUM 不在这里删 —— 它是上一个迁移创建的，answer_events/user_progress 还在用
