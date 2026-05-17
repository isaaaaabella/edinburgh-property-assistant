#!/usr/bin/env python3
"""
prepare_brief_data.py — 看房简报数据层（精简版）

职责：
  1. 读取 preferences.json
  2. 调用 fetch_area_data.py 获取 SIMD / 洪水 / 通勤数据
  3. 合并输出 JSON，供 /viewing-brief 和 /property-compare skill 使用

PDF 解析由 Claude 通过 Read 工具直接完成，本脚本不涉及。

用法：
  python prepare_brief_data.py --postcode "EH8 9NB"
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PREFERENCES_FILE = SCRIPT_DIR / "preferences.json"
FETCH_AREA_SCRIPT = SCRIPT_DIR / "fetch_area_data.py"


def load_preferences() -> dict:
    if PREFERENCES_FILE.exists():
        try:
            return json.loads(PREFERENCES_FILE.read_text())
        except Exception as e:
            return {"error": f"preferences.json 读取失败: {e}"}
    return {"error": "preferences.json 不存在"}


def fetch_area_data(postcode: str) -> dict:
    if not FETCH_AREA_SCRIPT.exists():
        return {"available": False, "error": "fetch_area_data.py 不存在"}
    try:
        result = subprocess.run(
            [sys.executable, str(FETCH_AREA_SCRIPT), "--postcode", postcode],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            data["available"] = True
            return data
        return {"available": False, "error": result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"available": False, "error": "fetch_area_data.py 超时（60s）"}
    except json.JSONDecodeError:
        return {"available": False, "error": "fetch_area_data.py 输出解析失败"}
    except Exception as e:
        return {"available": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="看房简报数据层")
    parser.add_argument("--postcode", required=True, help="苏格兰邮编，如 EH8 9NB")
    args = parser.parse_args()

    output = {
        "preferences": load_preferences(),
        "area_data": fetch_area_data(args.postcode),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
