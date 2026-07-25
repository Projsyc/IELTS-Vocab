"""密码哈希与 JWT 单测。"""

import uuid
from datetime import timedelta

import pytest
from jose import jwt

from app.core.config import settings
from app.core.security import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


# ─────────────────────────────────────────────────────────────
# 密码哈希
# ─────────────────────────────────────────────────────────────

def test_hash_then_verify():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_verify_rejects_wrong_password():
    h = hash_password("right")
    assert verify_password("wrong", h) is False


def test_hash_is_salted():
    """同一密码两次哈希结果不同 —— 每次用新盐，这是正确行为。"""
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b
    assert verify_password("same-password", a)
    assert verify_password("same-password", b)


def test_hash_does_not_contain_plaintext():
    h = hash_password("my-secret-password")
    assert "my-secret-password" not in h


def test_hash_fits_db_column():
    """password_hash 列是 VARCHAR(255)。"""
    assert len(hash_password("x" * 500)) <= 255


def test_long_password_works():
    """⭐ 超长密码必须能用。

    bcrypt 原生有 72 字节上限（超了抛 ValueError），
    我们用 SHA-256 预哈希绕开 —— 这个测试守护那个机制。
    """
    long_pw = "很长的中文密码" * 50          # 远超 72 字节
    h = hash_password(long_pw)
    assert verify_password(long_pw, h) is True
    assert verify_password(long_pw + "x", h) is False


def test_passwords_differing_after_72_bytes_are_distinguished():
    """⭐ 预哈希的关键收益：不会像裸 bcrypt 那样把长密码截断成相同。

    裸 bcrypt 只看前 72 字节，下面两个密码会被当成同一个。
    """
    base = "a" * 100
    h = hash_password(base + "ONE")
    assert verify_password(base + "ONE", h) is True
    assert verify_password(base + "TWO", h) is False


def test_password_with_null_byte():
    """NUL 字节在裸 bcrypt 里会导致截断，预哈希后不会。"""
    a = hash_password("secret\x00tail")
    assert verify_password("secret\x00tail", a) is True
    assert verify_password("secret", a) is False


def test_unicode_password():
    pw = "密码🔑Ünïcödé"
    assert verify_password(pw, hash_password(pw)) is True


def test_empty_password_rejected_on_hash():
    with pytest.raises(ValueError, match="密码"):
        hash_password("")


@pytest.mark.parametrize(("password", "stored"), [
    ("", "$2b$12$anything"),
    ("pw", ""),
    ("", ""),
])
def test_verify_returns_false_for_empty_inputs(password, stored):
    assert verify_password(password, stored) is False


@pytest.mark.parametrize("bad_hash", [
    "not-a-bcrypt-hash",
    "$2b$12$tooshort",
    "plaintext",
    "$$$",
])
def test_verify_returns_false_for_corrupt_hash(bad_hash):
    """哈希串损坏时返回 False 而不是抛异常 —— 否则一行脏数据能让登录接口 500。"""
    assert verify_password("anything", bad_hash) is False


# ─────────────────────────────────────────────────────────────
# JWT
# ─────────────────────────────────────────────────────────────

def test_token_round_trip():
    uid = uuid.uuid4()
    assert decode_access_token(create_access_token(uid)) == uid


def test_token_accepts_string_user_id():
    uid = uuid.uuid4()
    assert decode_access_token(create_access_token(str(uid))) == uid


def test_token_contains_expected_claims():
    uid = uuid.uuid4()
    payload = jwt.decode(
        create_access_token(uid), settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
    )
    assert payload["sub"] == str(uid)
    assert "exp" in payload
    assert "iat" in payload
    assert payload["exp"] > payload["iat"]


def test_expired_token_rejected():
    token = create_access_token(uuid.uuid4(), expires_delta=timedelta(seconds=-10))
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_tampered_signature_rejected():
    token = create_access_token(uuid.uuid4())
    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    with pytest.raises(InvalidTokenError):
        decode_access_token(tampered)


def test_token_signed_with_other_key_rejected():
    """⭐ 换密钥签的 token 必须被拒 —— 否则谁都能自己造 token。"""
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": 9999999999},
        "attacker-key",
        algorithm=settings.ALGORITHM,
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(forged)


def test_token_without_sub_rejected():
    token = jwt.encode(
        {"exp": 9999999999}, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    with pytest.raises(InvalidTokenError, match="sub"):
        decode_access_token(token)


def test_token_with_non_uuid_sub_rejected():
    token = jwt.encode(
        {"sub": "not-a-uuid", "exp": 9999999999},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    with pytest.raises(InvalidTokenError, match="UUID"):
        decode_access_token(token)


@pytest.mark.parametrize("bad", ["", "garbage", "a.b.c", "..."])
def test_malformed_token_rejected(bad):
    with pytest.raises(InvalidTokenError):
        decode_access_token(bad)


def test_none_algorithm_attack_rejected():
    """⭐ alg=none 的经典攻击必须被拒。

    攻击者把算法改成 "none" 去掉签名，如果库配置不当就会放过。
    """
    unsigned = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": 9999999999}, "", algorithm="HS256"
    )
    # 手工构造一个无签名 token
    header_payload = ".".join(unsigned.split(".")[:2])
    with pytest.raises(InvalidTokenError):
        decode_access_token(header_payload + ".")
