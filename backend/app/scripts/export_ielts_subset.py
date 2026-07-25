"""从完整的 ECDICT 导出雅思子集，供仓库提交。

为什么要这个脚本：
    完整的 ecdict.csv 是 66MB / 770,611 行，其中 99.3% 是我们永远不碰的非雅思词。
    只导出 tag 含 ielts 的 5,040 行 → **1.5MB**，可以直接提交进 git。

提交子集而非"运行时下载"的理由（见 docs/08-decisions.md ADR-011）：
    1. fresh clone 离线可复现，不依赖 raw.githubusercontent.com 还活着
    2. 等于锁定了数据版本 —— 文档里那些实测数字（5,040 → 剔 272 → 4,768）永远对得上
    3. ECDICT 是 MIT 许可，再分发没问题

用法：
    # 需要先有完整的 ecdict.csv（seed 脚本会自动下载，或手动放到 data/）
    backend/.venv/bin/python -m app.scripts.export_ielts_subset

    # 校验现有子集是否与完整版一致（CI / 定期检查用）
    backend/.venv/bin/python -m app.scripts.export_ielts_subset --verify
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

from app.scripts.ecdict import has_ielts_tag

BACKEND_DIR = Path(__file__).resolve().parents[2]
FULL_CSV = BACKEND_DIR / "data" / "ecdict.csv"
SUBSET_CSV = BACKEND_DIR / "data" / "ecdict-ielts.csv"


def export(full: Path, out: Path) -> tuple[int, str]:
    """导出 ielts 子集，保留 ECDICT 的全部列。返回 (行数, sha256)。

    保留全部列而非只留用到的 7 列：多出的 0.8MB 无所谓，
    但以后想用 definition（英文释义）/ collins（星级）/ oxford 时不必重新导出。
    """
    csv.field_size_limit(10**9)

    with full.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if not fieldnames:
            raise SystemExit(f"❌ {full} 没有表头，文件可能损坏")
        rows = [r for r in reader if has_ielts_tag(r.get("tag"))]

    if not rows:
        raise SystemExit("❌ 没筛出任何 ielts 词条，检查源文件是否正确")

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".csv.part")
    # newline="" + \n：保证跨平台产出字节一致，否则 Windows 上导出的 sha256 会不同
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(out)

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    return len(rows), digest


def main() -> int:
    p = argparse.ArgumentParser(description="从完整 ECDICT 导出雅思子集")
    p.add_argument("--verify", action="store_true",
                   help="只校验现有子集与完整版是否一致，不覆盖")
    args = p.parse_args()

    if not FULL_CSV.exists():
        print(f"❌ 找不到完整的 ECDICT：{FULL_CSV}")
        print("   先跑一次 seed 脚本让它自动下载，或手动放到该路径。")
        return 1

    if args.verify:
        if not SUBSET_CSV.exists():
            print(f"❌ 子集不存在：{SUBSET_CSV}")
            return 1
        old = hashlib.sha256(SUBSET_CSV.read_bytes()).hexdigest()
        tmp = SUBSET_CSV.with_suffix(".verify.csv")
        try:
            count, new = export(FULL_CSV, tmp)
        finally:
            tmp.unlink(missing_ok=True)
        if old == new:
            print(f"✅ 子集与完整版一致（{count:,} 行，sha256 {new[:16]}…）")
            return 0
        print("⚠️  子集与完整版**不一致** —— 上游 ECDICT 可能更新了")
        print(f"    仓库内: {old}")
        print(f"    重新导出: {new}")
        print("    若确认要采用新数据，跑一次不带 --verify 的导出，并复核文档里的实测数字。")
        return 1

    count, digest = export(FULL_CSV, SUBSET_CSV)
    size_mb = SUBSET_CSV.stat().st_size / 1e6
    print(f"✅ 导出 {count:,} 个 ielts 词条 → {SUBSET_CSV.name}（{size_mb:.1f} MB）")
    print(f"   sha256: {digest}")
    print("\n把上面的 sha256 更新到 data/README.md 里。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
