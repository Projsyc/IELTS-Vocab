-- 容器首次初始化时自动执行（只跑一次）
--
-- gen_random_uuid() 在 PostgreSQL 13+ 已内置，无需 pgcrypto。
-- 这里显式建扩展是为了兼容更早版本，且 IF NOT EXISTS 保证幂等。
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 验证 UUID 生成可用（失败会让容器初始化报错，早暴露问题）
DO $$
BEGIN
    PERFORM gen_random_uuid();
END $$;
