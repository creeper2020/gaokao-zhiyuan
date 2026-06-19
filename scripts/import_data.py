#!/usr/bin/env python3
"""
Import gaokao data from Zhangxuefeng-AI-gaokao SQLite database.

Transforms data from gaokao_2025.db into the format needed by gaokao-zhiyuan:
  - CSV files per province: data/{province}/table1_{province}.csv
  - JSON score-rank tables: data/{province}/ranking_score_{province}_{physics|history}.json

Source: https://github.com/Pilot1799/Zhangxuefeng-AI-gaokao
Database: gaokao_2025.db (SQLite, ~180MB)

Usage:
    python3 scripts/import_data.py [--db /path/to/gaokao_2025.db] [--output data/]
"""

import sqlite3
import csv
import json
import os
import sys
import argparse
import hashlib
from collections import defaultdict
from pathlib import Path

# ============================================================
# Province name mapping: Chinese name → pinyin directory name
# ============================================================
PROVINCE_DIR_MAP = {
    "北京": "beijing",
    "天津": "tianjin",
    "上海": "shanghai",
    "重庆": "chongqing",
    "河北": "hebei",
    "山西": "shanxi",
    "辽宁": "liaoning",
    "吉林": "jilin",
    "黑龙江": "heilongjiang",
    "江苏": "jiangsu",
    "浙江": "zhejiang",
    "安徽": "anhui",
    "福建": "fujian",
    "江西": "jiangxi",
    "山东": "shandong",
    "河南": "henan",
    "湖北": "hubei",
    "湖南": "hunan",
    "广东": "guangdong",
    "海南": "hainan",
    "四川": "sichuan",
    "贵州": "guizhou",
    "云南": "yunnan",
    "陕西": "shaanxi",
    "甘肃": "gansu",
    "青海": "qinghai",
    "台湾": "taiwan",
    "内蒙古": "neimenggu",
    "广西": "guangxi",
    "西藏": "xizang",
    "宁夏": "ningxia",
    "新疆": "xinjiang",
}

# ============================================================
# Subject mapping: DB value → target category (物理/历史)
# ============================================================
SUBJECT_MAP = {
    "物理类": "物理",
    "物理": "物理",
    "理科": "物理",
    "历史类": "历史",
    "历史": "历史",
    "文科": "历史",
    "综合改革": "综合",  # New gaokao provinces (no physics/history split)
    "综合": "综合",
}

# For 综合改革 provinces, we map to 物理 for the JSON file naming
SUBJECT_JSON_MAP = {
    "物理类": "physics",
    "物理": "physics",
    "理科": "physics",
    "历史类": "history",
    "历史": "history",
    "文科": "history",
    "综合改革": "physics",  # Unified, use physics as default
    "综合": "physics",
}

# ============================================================
# Batch normalization
# ============================================================
BATCH_NORMALIZE = {
    "本科批": "本科批",
    "本科一批": "本科批",
    "本科二批": "本科批",
    "本科提前批": "本科提前批",
    "专科批": "专科批",
    "专科提前批": "专科提前批",
    "常规批": "本科批",
    "普通类一段": "本科批",
    "平行录取一段": "本科批",
    "本科一段": "本科批",
    "本科批A段": "本科批",
    "本科批C段": "本科批",
}


def normalize_batch(batch: str) -> str:
    """Normalize batch name to standard form."""
    if not batch:
        return "本科批"
    for key, val in BATCH_NORMALIZE.items():
        if key in batch:
            return val
    if "本科" in batch:
        return "本科批"
    if "专科" in batch:
        return "专科批"
    return batch


def generate_school_code(school_name: str) -> str:
    """Generate a deterministic school code from school name."""
    h = hashlib.md5(school_name.encode("utf-8")).hexdigest()[:6].upper()
    return f"X{h}"


def generate_major_code(major_name: str) -> str:
    """Generate a deterministic major code from major name."""
    h = hashlib.md5(major_name.encode("utf-8")).hexdigest()[:4].upper()
    return h


def generate_id(province: str, school: str, major: str, idx: int) -> int:
    """Generate a unique numeric ID."""
    return idx + 8000000  # Offset to avoid collision with existing data


def classify_major(major_name: str) -> dict:
    """Classify a major into categories based on name heuristics."""
    name = major_name or ""
    return {
        "is_science": any(kw in name for kw in ["数学", "物理", "化学", "生物", "统计", "天文", "地球"]),
        "is_engineering": any(kw in name for kw in ["工程", "计算机", "电子", "机械", "自动", "信息", "软件", "网络", "智能", "机器人", "材料", "能源", "电气", "通信", "控制", "航空航天", "土木", "水利", "环境", "核", "光学"]),
        "is_medical": any(kw in name for kw in ["医", "临床", "口腔", "药学", "护理", "公共卫生", "康复"]),
        "is_economics_mgmt_law": any(kw in name for kw in ["经济", "金融", "管理", "会计", "法学", "法律", "工商", "市场", "贸易", "审计", "财税", "保险", "投资"]),
        "is_liberal_arts": any(kw in name for kw in ["文学", "历史", "哲学", "教育", "新闻", "传播", "社会", "政治", "马克思主义", "中文", "汉语"]),
        "is_design_arts": any(kw in name for kw in ["设计", "艺术", "美术", "音乐", "戏剧", "影视", "动画", "传媒", "摄影", "舞蹈"]),
        "is_language": any(kw in name for kw in ["英语", "日语", "法语", "德语", "俄语", "西班牙", "翻译", "外国语言", "商务英语", "韩语", "葡萄牙", "阿拉伯"]),
    }


def extract_major_description(major_name: str) -> str:
    """Extract description/notes from major name (content in parentheses)."""
    if not major_name:
        return ""
    # Extract content within parentheses
    import re
    matches = re.findall(r'[（(]([^）)]+)[）)]', major_name)
    return "; ".join(matches) if matches else ""


def load_sqlite_data(db_path: str) -> dict:
    """Load all data from the SQLite database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print(f"Loading data from {db_path}...")

    # Load major_scores
    cursor.execute("SELECT * FROM major_scores")
    major_scores = [dict(row) for row in cursor.fetchall()]
    print(f"  major_scores: {len(major_scores):,} rows")

    # Load school_scores
    cursor.execute("SELECT * FROM school_scores")
    school_scores = [dict(row) for row in cursor.fetchall()]
    print(f"  school_scores: {len(school_scores):,} rows")

    # Load province_lines
    cursor.execute("SELECT * FROM province_lines")
    province_lines = [dict(row) for row in cursor.fetchall()]
    print(f"  province_lines: {len(province_lines):,} rows")

    conn.close()

    return {
        "major_scores": major_scores,
        "school_scores": school_scores,
        "province_lines": province_lines,
    }


def build_school_info(school_scores: list) -> dict:
    """Build a lookup dict of school info from school_scores."""
    school_info = {}
    for row in school_scores:
        name = row["school"]
        if name not in school_info:
            school_info[name] = {
                "school": name,
                "province": row.get("province", ""),
            }
    return school_info


def transform_to_csv_rows(major_scores: list, school_scores: list) -> dict:
    """
    Transform SQLite data into CSV rows grouped by province.

    Returns: {province_name: [csv_row_dicts]}
    """
    # Build school-level info lookup: (school, province) -> school_score record
    school_lookup = {}
    for row in school_scores:
        key = (row["school"], row["province"])
        if key not in school_lookup:
            school_lookup[key] = row

    # Group major_scores by province
    province_data = defaultdict(list)
    idx = 0

    for row in major_scores:
        province = row["province"]
        school = row["school"]
        major = row["major"]
        subject_raw = row["subject"]
        batch = row["batch"]
        year = row["year"]

        # Normalize subject
        subject_category = SUBJECT_MAP.get(subject_raw, "物理")
        if subject_category == "综合":
            # For 综合改革 provinces, treat as 物理 for the table
            subject_category = "物理"

        # Normalize batch
        batch_normalized = normalize_batch(batch)

        # Get school-level score data
        school_key = (school, province)
        school_rec = school_lookup.get(school_key, {})

        # Determine education level
        education_level = "本科"
        if "专科" in batch:
            education_level = "专科"

        # Classify major
        classification = classify_major(major)

        # Generate codes
        school_code = generate_school_code(school)
        major_code = generate_major_code(major)
        major_group = row.get("major_group") or ""

        # Build CSV row matching the existing format
        csv_row = {
            "id": generate_id(province, school, major, idx),
            "生源地": province,
            "批次": batch_normalized,
            "科类": subject_category,
            "选科限制": "",  # Not available in SQLite DB
            "院校代码": school_code,
            "专业组代码": major_group,
            "院校名称": school,
            "专业代码": major_code,
            "专业名称": major,
            "专业备注": extract_major_description(major),
            "学制": "",
            "学费": "",
            "计划数": row.get("plan_count") or "",
            "新增专业": "",
            "录取最低分": row.get("min_score") or "",
            "录取最低位次": row.get("min_rank") or "",
            "专业组最低分": "",
            "专业组最低位次": "",
            "专业组最低分.1": "",
            "专业组最低位次.1": "",
            "计划数.1": "",
            "最低分": row.get("min_score") or "",
            "最低位次": row.get("min_rank") or "",
            "计划数.2": row.get("plan_count") or "",
            "最低分.1": "",
            "最低位次.1": "",
            "计划数.3": "",
            "最低分.2": "",
            "最低位次.2": "",
            "所在省": "",
            "城市": "",
            "本科/专科": education_level,
            "隶属单位": "",
            "院校标签": "",
            "类型": "",
            "公私性质": "",
            "院校水平": "",
        }

        province_data[province].append(csv_row)
        idx += 1

    return dict(province_data)


def generate_score_rank_json(major_scores: list) -> dict:
    """
    Generate 一分一段表 (score-rank distribution) JSON from major_scores data.

    For each (province, subject), collect unique (min_score, min_rank) pairs
    and build a cumulative distribution table.

    Returns: {(province, subject_json): [{"score": str, "num": int, "accumulate": int}]}
    """
    # Collect all (province, subject, score, rank) tuples
    score_rank_data = defaultdict(set)

    for row in major_scores:
        province = row["province"]
        subject_raw = row["subject"]
        score = row.get("min_score")
        rank = row.get("min_rank")

        if score and rank and score > 0 and rank > 0:
            subject_json = SUBJECT_JSON_MAP.get(subject_raw, "physics")
            score_rank_data[(province, subject_json)].add((score, rank))

    # Build distribution tables
    result = {}
    for (province, subject_json), score_rank_set in score_rank_data.items():
        # Sort by score descending
        sorted_pairs = sorted(score_rank_set, key=lambda x: -x[0])

        if not sorted_pairs:
            continue

        # Build the distribution table
        # Group by score, take the best (smallest) rank for each score
        score_to_best_rank = {}
        for score, rank in sorted_pairs:
            if score not in score_to_best_rank or rank < score_to_best_rank[score]:
                score_to_best_rank[score] = rank

        # Sort by score descending
        sorted_scores = sorted(score_to_best_rank.items(), key=lambda x: -x[0])

        # Build the JSON structure matching the existing format
        entries = []
        accumulate = 0
        for i, (score, rank) in enumerate(sorted_scores):
            # For the top score, use the rank directly as accumulate
            if i == 0:
                accumulate = rank
                num = rank
            else:
                # Estimate num from difference in accumulate
                prev_accumulate = entries[-1]["accumulate"]
                num = max(1, rank - prev_accumulate)
                accumulate = rank

            score_str = str(score)
            if i == 0 and len(sorted_scores) > 1:
                # Top score range like "695-750"
                next_score = sorted_scores[1][0]
                score_str = f"{score}-{max(score + 5, 750)}"

            entries.append({
                "score": score_str,
                "num": num,
                "accumulate": accumulate,
            })

        result[(province, subject_json)] = entries

    return result


def write_csv_files(province_data: dict, output_dir: str):
    """Write CSV files per province."""
    # Use the same 38 fields as the existing table1 format
    FIELDNAMES = [
        "id", "生源地", "批次", "科类", "选科限制", "院校代码", "专业组代码",
        "院校名称", "专业代码", "专业名称", "专业备注", "学制", "学费",
        "计划数", "新增专业", "录取最低分", "录取最低位次",
        "专业组最低分", "专业组最低位次", "专业组最低分.1", "专业组最低位次.1",
        "计划数.1", "最低分", "最低位次", "计划数.2", "最低分.1", "最低位次.1",
        "计划数.3", "最低分.2", "最低位次.2", "所在省", "城市", "本科/专科",
        "隶属单位", "院校标签", "类型", "公私性质", "院校水平",
    ]

    for province_name, rows in province_data.items():
        province_dir_name = PROVINCE_DIR_MAP.get(province_name)
        if not province_dir_name:
            print(f"  WARNING: No directory mapping for province '{province_name}', skipping")
            continue

        province_dir = os.path.join(output_dir, province_dir_name)
        os.makedirs(province_dir, exist_ok=True)

        csv_path = os.path.join(province_dir, f"table1_{province_dir_name}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)

        print(f"  Wrote {len(rows):,} rows to {csv_path}")


def write_score_rank_json(score_rank_data: dict, output_dir: str):
    """Write 一分一段表 JSON files per province."""
    for (province_name, subject_json), entries in score_rank_data.items():
        province_dir_name = PROVINCE_DIR_MAP.get(province_name)
        if not province_dir_name:
            continue

        province_dir = os.path.join(output_dir, province_dir_name)
        os.makedirs(province_dir, exist_ok=True)

        json_path = os.path.join(
            province_dir,
            f"ranking_score_{province_dir_name}_{subject_json}.json"
        )

        # Sort entries by score descending for the JSON
        sorted_entries = sorted(entries, key=lambda x: -parse_score_for_sort(x["score"]))

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"data": sorted_entries}, f, ensure_ascii=False, indent=2)

        print(f"  Wrote {len(entries)} score-rank entries to {json_path}")


def parse_score_for_sort(score_str: str) -> int:
    """Parse score string for sorting (handle ranges like '695-750')."""
    if "-" in str(score_str):
        try:
            return int(str(score_str).split("-")[0])
        except ValueError:
            return 0
    try:
        return int(score_str)
    except (ValueError, TypeError):
        return 0


def write_province_lines_json(province_lines: list, output_dir: str):
    """Write province control lines as a reference JSON."""
    lines_by_province = defaultdict(list)
    for row in province_lines:
        lines_by_province[row["province"]].append({
            "year": row["year"],
            "batch": row["batch"],
            "subject": row["subject"],
            "score_line": row["score_line"],
        })

    # Write a single reference file
    output_path = os.path.join(output_dir, "province_lines_2025.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dict(lines_by_province), f, ensure_ascii=False, indent=2)
    print(f"  Wrote province lines to {output_path}")


def print_summary(data: dict):
    """Print a summary of the data."""
    major_scores = data["major_scores"]
    school_scores = data["school_scores"]
    province_lines = data["province_lines"]

    print("\n" + "=" * 60)
    print("DATABASE SUMMARY")
    print("=" * 60)

    # Provinces
    provinces = sorted(set(r["province"] for r in major_scores))
    print(f"\nProvinces: {len(provinces)}")
    for p in provinces:
        cnt = sum(1 for r in major_scores if r["province"] == p)
        print(f"  {p}: {cnt:,} major records")

    # Years
    years = sorted(set(r["year"] for r in major_scores))
    print(f"\nYears: {years}")

    # Subjects
    subjects = sorted(set(r["subject"] for r in major_scores))
    print(f"\nSubjects: {subjects}")

    # Score range
    scores = [r["min_score"] for r in major_scores if r.get("min_score")]
    ranks = [r["min_rank"] for r in major_scores if r.get("min_rank")]
    if scores:
        print(f"\nScore range: {min(scores)} - {max(scores)}")
    if ranks:
        print(f"Rank range: {min(ranks)} - {max(ranks):,}")

    # Province lines
    print(f"\nProvince control lines: {len(province_lines)} entries")
    print(f"School-level scores: {len(school_scores):,} entries")


def main():
    parser = argparse.ArgumentParser(description="Import gaokao data from SQLite to CSV/JSON")
    parser.add_argument("--db", default="/opt/data/workspace/gaokao_data/gaokao_2025.db",
                        help="Path to gaokao_2025.db SQLite database")
    parser.add_argument("--output", default=None,
                        help="Output directory for data files (default: gaokao-zhiyuan/data/)")
    parser.add_argument("--summary-only", action="store_true",
                        help="Only print database summary, don't export")
    parser.add_argument("--province", default=None,
                        help="Only process a specific province (Chinese name)")
    args = parser.parse_args()

    # Resolve paths
    script_dir = Path(__file__).parent
    project_dir = script_dir.parent

    if args.output:
        output_dir = args.output
    else:
        output_dir = str(project_dir / "data")

    db_path = args.db

    if not os.path.exists(db_path):
        print(f"ERROR: Database not found: {db_path}")
        print("Download from: https://github.com/Pilot1799/Zhangxuefeng-AI-gaokao/releases")
        sys.exit(1)

    # Load data
    data = load_sqlite_data(db_path)

    # Print summary
    print_summary(data)

    if args.summary_only:
        return

    # Filter by province if specified
    if args.province:
        data["major_scores"] = [r for r in data["major_scores"] if r["province"] == args.province]
        data["school_scores"] = [r for r in data["school_scores"] if r["province"] == args.province]
        data["province_lines"] = [r for r in data["province_lines"] if r["province"] == args.province]
        print(f"\nFiltered to province: {args.province}")

    # Transform to CSV format
    print("\n" + "=" * 60)
    print("TRANSFORMING DATA")
    print("=" * 60)

    print("\nGenerating CSV files...")
    province_data = transform_to_csv_rows(data["major_scores"], data["school_scores"])
    write_csv_files(province_data, output_dir)

    # Generate score-rank JSON
    print("\nGenerating score-rank JSON files...")
    score_rank_data = generate_score_rank_json(data["major_scores"])
    write_score_rank_json(score_rank_data, output_dir)

    # Write province lines reference
    print("\nWriting province control lines...")
    write_province_lines_json(data["province_lines"], output_dir)

    print("\n" + "=" * 60)
    print("IMPORT COMPLETE")
    print("=" * 60)
    print(f"\nOutput directory: {output_dir}")
    print(f"Provinces processed: {len(province_data)}")

    # List generated files
    total_csv_rows = sum(len(rows) for rows in province_data.values())
    total_json_files = len(score_rank_data)
    print(f"Total CSV rows: {total_csv_rows:,}")
    print(f"Total score-rank JSON files: {total_json_files}")


if __name__ == "__main__":
    main()
