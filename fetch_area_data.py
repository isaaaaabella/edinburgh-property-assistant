#!/usr/bin/env python3
"""
买房助手 - 区域情报抓取器
供 /home-report skill 使用，输出 JSON 到 stdout。

功能：
  1. postcodes.io → 坐标 + 苏格兰数据区（Data Zone）
  2. SIMD 2020 → 综合剥夺分位、犯罪分位（1=最差, 10=最好）
  3. SEPA → 洪水风险
  4. Google Maps Directions API → 通勤时间（需 .env 中配置 GOOGLE_MAPS_API_KEY）

用法:
  python fetch_area_data.py --postcode "EH8 9NB"
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import csv
import io
from datetime import datetime, timezone

# ── 加载 .env ─────────────────────────────────────────────────────────────
def load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    config = {}
    if not os.path.exists(env_path):
        return config
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                config[key.strip()] = value.strip()
    return config

# ── 加载 preferences.json ────────────────────────────────────────────────
def load_preferences():
    prefs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preferences.json")
    if not os.path.exists(prefs_path):
        return {}
    with open(prefs_path) as f:
        return json.load(f)

# ── 简单 HTTP GET（不依赖 requests 库）──────────────────────────────────
def http_get(url, timeout=10):
    """返回 (status_code, text)，出错返回 (None, error_msg)"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, str(e)
    except Exception as e:
        return None, str(e)

# ── Step 1: postcodes.io → 坐标 + Data Zone ───────────────────────────
def get_postcode_data(postcode):
    clean = postcode.replace(" ", "").upper()
    url = f"https://api.postcodes.io/postcodes/{urllib.parse.quote(clean)}"
    status, text = http_get(url)
    if status != 200:
        return None, f"postcodes.io 失败 ({status}): {text[:200]}"
    try:
        data = json.loads(text)
        result = data["result"]
        codes = result.get("codes", {})
        return {
            "lat": result["latitude"],
            "lon": result["longitude"],
            # SIMD 2020 uses 2011 Data Zones (lsoa11); lsoa is the 2021 code
            "data_zone": codes.get("lsoa11") or codes.get("lsoa"),
            "admin_district": result.get("admin_district"),
            "admin_district_code": codes.get("admin_district"),
        }, None
    except Exception as e:
        return None, f"postcodes.io 解析失败: {e}"

# ── Step 2: SIMD 2020 ──────────────────────────────────────────────────
SIMD_CACHE_PATH = os.path.expanduser("~/.property_assistant_simd_cache.json")
SIMD_CACHE_MAX_AGE_DAYS = 365

# 已知的 SIMD 2020 CSV 下载 URL（按优先级尝试）
# 注意：苏格兰政府官方下载链接经 S3 重定向会返回 403，使用 NHS Scotland 开放数据替代
# NHS Scotland CSV 包含：DataZone, SIMD2020V2Rank, SIMD2020V2CountryDecile（综合排名/分位）
# 不含 domain-specific 犯罪/收入分位（这些在 Excel 文件中，无法直接 CSV 获取）
SIMD_CSV_URLS = [
    # NHS Scotland 开放数据平台（测试可用 ✅）
    "https://www.opendata.nhs.scot/dataset/78d41fa9-1a62-4f7b-9edb-3e8522a93378/resource/acade396-8430-4b34-895a-b3e757fa346e/download/simd2020v2_22062020.csv",
]

def load_simd_cache():
    """加载本地 SIMD 缓存，返回 dict[data_zone → {overall_rank, overall_decile, crime_decile, ...}]"""
    if not os.path.exists(SIMD_CACHE_PATH):
        return None

    # 检查缓存年龄
    age_days = (time.time() - os.path.getmtime(SIMD_CACHE_PATH)) / 86400
    if age_days > SIMD_CACHE_MAX_AGE_DAYS:
        sys.stderr.write(f"⚠️  SIMD 缓存已超过 {SIMD_CACHE_MAX_AGE_DAYS} 天，将重新下载\n")
        return None

    try:
        with open(SIMD_CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return None

def download_simd_csv():
    """
    尝试下载 SIMD CSV，解析后保存到本地缓存。
    返回 dict 或 None（失败时）。
    """
    sys.stderr.write("📥 正在下载 SIMD 2020 数据（首次运行，约 1MB）...\n")

    csv_text = None
    for url in SIMD_CSV_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                csv_text = resp.read().decode("utf-8", errors="replace")
                if len(csv_text) > 10000:  # 至少有点内容
                    sys.stderr.write(f"✅ 下载成功: {url[:60]}...\n")
                    break
        except Exception as e:
            sys.stderr.write(f"⚠️  URL 失败: {url[:60]}... ({e})\n")
            continue

    if not csv_text:
        return None

    return parse_simd_csv(csv_text)

def parse_simd_csv(csv_text):
    """
    解析 SIMD CSV，构建 data_zone → ranks 的字典。
    支持多种列名格式（苏格兰政府不同版本列名不同）。
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    simd_dict = {}

    # 列名映射（NHS Scotland CSV 列名 + 备用）
    col_aliases = {
        "data_zone": ["DataZone", "Data_Zone", "data_zone", "DZ"],
        "overall_rank": ["SIMD2020V2Rank", "SIMD2020v2_Rank", "Overall_SIMD20_Rank", "Rank"],
        "overall_decile": ["SIMD2020V2CountryDecile", "SIMD2020_CountryDecile"],
        # 犯罪/收入 domain 分位在 NHS Scotland CSV 中不存在（仅 Excel 文件含有）
        "crime_rank": ["SIMD2020_Crime_Domain_Rank", "Crime_Domain_Rank"],
        "income_rank": ["SIMD2020_Income_Domain_Rank", "Income_Domain_Rank"],
    }

    headers = reader.fieldnames or []
    sys.stderr.write(f"  CSV 列名: {headers[:10]}\n")

    def find_col(aliases):
        for a in aliases:
            if a in headers:
                return a
        return None

    dz_col = find_col(col_aliases["data_zone"])
    rank_col = find_col(col_aliases["overall_rank"])
    decile_col = find_col(col_aliases["overall_decile"])
    crime_col = find_col(col_aliases["crime_rank"])
    income_col = find_col(col_aliases["income_rank"])

    if not dz_col:
        sys.stderr.write(f"⚠️  找不到 Data Zone 列，已有: {headers}\n")
        return None

    total = 0
    for row in reader:
        dz = row.get(dz_col, "").strip()
        if not dz.startswith("S01"):
            continue
        entry = {}
        if rank_col and row.get(rank_col):
            try:
                entry["overall_rank"] = int(float(row[rank_col]))
            except ValueError:
                pass
        if decile_col and row.get(decile_col):
            try:
                # NHS Scotland CSV decile: 1=most deprived, 10=least deprived (consistent with our convention)
                entry["overall_decile"] = int(float(row[decile_col]))
            except ValueError:
                pass
        if crime_col and row.get(crime_col):
            try:
                entry["crime_rank"] = int(float(row[crime_col]))
            except ValueError:
                pass
        if income_col and row.get(income_col):
            try:
                entry["income_rank"] = int(float(row[income_col]))
            except ValueError:
                pass
        if entry:
            simd_dict[dz] = entry
            total += 1

    sys.stderr.write(f"  解析完成：{total} 个数据区\n")

    if total < 100:
        sys.stderr.write("⚠️  解析结果太少，CSV 格式可能不对\n")
        return None

    # 如果 CSV 没有直接提供 decile，从 rank 计算
    # SIMD rank: 1=最剥夺, 6976=最不剥夺；decile 10=最不剥夺（最好）
    total_zones = len(simd_dict)
    for dz, entry in simd_dict.items():
        if "overall_rank" in entry and "overall_decile" not in entry:
            entry["overall_decile"] = min(10, max(1, int((entry["overall_rank"] - 1) / total_zones * 10) + 1))
        if "crime_rank" in entry:
            entry["crime_decile"] = min(10, max(1, int((entry["crime_rank"] - 1) / total_zones * 10) + 1))
        if "income_rank" in entry:
            entry["income_decile"] = min(10, max(1, int((entry["income_rank"] - 1) / total_zones * 10) + 1))

    # 保存缓存
    try:
        with open(SIMD_CACHE_PATH, "w") as f:
            json.dump(simd_dict, f)
        sys.stderr.write(f"✅ SIMD 缓存已保存至 {SIMD_CACHE_PATH}\n")
    except Exception as e:
        sys.stderr.write(f"⚠️  缓存写入失败: {e}\n")

    return simd_dict

def get_simd(data_zone):
    """返回 SIMD 数据 dict 或 None"""
    if not data_zone or not data_zone.startswith("S01"):
        return None

    simd_dict = load_simd_cache()
    if simd_dict is None:
        simd_dict = download_simd_csv()

    if simd_dict is None:
        return None

    return simd_dict.get(data_zone)

# ── Step 3: SEPA 洪水风险 ─────────────────────────────────────────────
# 已验证的 SEPA ArcGIS FeatureServer（测试于 2026-04-08）
# Org: GbTQcjO6QsZgVZ1N（East Lothian Council 托管的 SEPA flood layers）
SEPA_FLOOD_LAYERS = [
    {
        "url": "https://services-eu1.arcgis.com/GbTQcjO6QsZgVZ1N/arcgis/rest/services/SEPA_River_Flooding_Extent/FeatureServer/42/query",
        "type": "river",
    },
    {
        "url": "https://services-eu1.arcgis.com/GbTQcjO6QsZgVZ1N/arcgis/rest/services/SEPA_Surface_Water_Flooding_Extent/FeatureServer/43/query",
        "type": "surface_water",
    },
    {
        "url": "https://services-eu1.arcgis.com/GbTQcjO6QsZgVZ1N/arcgis/rest/services/SEPA_Coastal_Flooding_Extent/FeatureServer/41/query",
        "type": "coastal",
    },
]

def get_flood_risk(lat, lon):
    """
    查询 SEPA 洪水风险（河流、地表水、海岸）。
    返回 {"risk": "none"/"low"/"medium"/"high", "zone": str|None, "types": []}
    """
    common_params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "false",
        "outFields": "BAND_DESC,PROB,MAP_TYPE,SCENARIO",
        "f": "json",
    }

    flood_types_found = []
    any_request_succeeded = False

    for layer in SEPA_FLOOD_LAYERS:
        params = urllib.parse.urlencode(common_params)
        url = f"{layer['url']}?{params}"
        status, text = http_get(url, timeout=10)
        if status == 200:
            any_request_succeeded = True
            try:
                data = json.loads(text)
                features = data.get("features", [])
                if features:
                    attrs = features[0].get("attributes", {})
                    band = str(attrs.get("BAND_DESC") or attrs.get("PROB") or "")
                    flood_types_found.append({
                        "type": layer["type"],
                        "band": band,
                    })
            except Exception:
                continue

    if not any_request_succeeded:
        return {"risk": "unknown", "zone": None, "types": []}

    if not flood_types_found:
        return {"risk": "none", "zone": None, "types": []}

    # 判断最高风险等级
    # SEPA BAND_DESC 通常为 "High" / "Medium" / "Low" 或概率描述
    risk_level = "low"
    for ft in flood_types_found:
        band_lower = ft["band"].lower()
        if "high" in band_lower or "0.5%" in band_lower or "200" in band_lower:
            risk_level = "high"
            break
        elif "medium" in band_lower or "0.1%" in band_lower:
            if risk_level != "high":
                risk_level = "medium"

    type_labels = {"river": "河流", "surface_water": "地表水", "coastal": "海岸"}
    zone_desc = "+".join(type_labels.get(ft["type"], ft["type"]) for ft in flood_types_found)

    return {"risk": risk_level, "zone": zone_desc, "types": flood_types_found}

# ── Step 4: Google Maps 通勤 ─────────────────────────────────────────
def get_commute(lat, lon, postcode, prefs, gmaps_key):
    """
    查询到两个工作地点的公交通勤时间。
    返回 {"user": {...}, "partner": {...}} 或 None
    """
    if not gmaps_key:
        return None

    commute_cfg = prefs.get("commute", {})
    user_dest = commute_cfg.get("user_workplace", "15 Lauriston Pl, Edinburgh EH3 9EN")
    partner_dest = commute_cfg.get("partner_workplace", "Heriot-Watt University, Edinburgh EH14 4AS")
    departure_ts = commute_cfg.get("departure_weekday_8_30_unix", 1745913000)

    origin = urllib.parse.quote(f"{postcode}, Edinburgh")

    results = {}
    for label, dest in [("user", user_dest), ("partner", partner_dest)]:
        params = urllib.parse.urlencode({
            "origin": f"{lat},{lon}",
            "destination": dest,
            "mode": "transit",
            "departure_time": departure_ts,
            "key": gmaps_key,
        })
        url = f"https://maps.googleapis.com/maps/api/directions/json?{params}"
        status, text = http_get(url, timeout=10)

        if status != 200:
            results[label] = {"min": None, "route": None, "error": f"HTTP {status}"}
            continue

        try:
            data = json.loads(text)
            if data.get("status") != "OK":
                results[label] = {"min": None, "route": None, "error": data.get("status")}
                continue

            leg = data["routes"][0]["legs"][0]
            duration_sec = leg["duration"]["value"]
            duration_min = round(duration_sec / 60)

            # 提取主要公交路线名称
            steps = leg.get("steps", [])
            transit_lines = []
            for step in steps:
                if step.get("travel_mode") == "TRANSIT":
                    line = step.get("transit_details", {}).get("line", {})
                    short_name = line.get("short_name") or line.get("name") or ""
                    if short_name:
                        transit_lines.append(short_name)

            route_str = " → ".join(transit_lines) if transit_lines else "公交+步行"
            results[label] = {"min": duration_min, "route": route_str, "error": None}

        except Exception as e:
            results[label] = {"min": None, "route": None, "error": str(e)}

    return results

# ── 主逻辑 ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="获取房产区域数据（SIMD、洪水、通勤）")
    parser.add_argument("--postcode", required=True, help="英国邮编，如 'EH8 9NB'")
    parser.add_argument("--skip-simd", action="store_true", help="跳过 SIMD 数据（加快速度）")
    parser.add_argument("--skip-flood", action="store_true", help="跳过洪水风险查询")
    parser.add_argument("--skip-commute", action="store_true", help="跳过通勤查询")
    args = parser.parse_args()

    config = load_env()
    prefs = load_preferences()
    gmaps_key = config.get("GOOGLE_MAPS_API_KEY", "")

    postcode = args.postcode.strip()
    output = {
        "postcode": postcode,
        "data_zone": None,
        "lat": None,
        "lon": None,
        "admin_district": None,
        "simd_overall_rank": None,
        "simd_overall_decile": None,
        "simd_crime_decile": None,
        "simd_income_decile": None,
        "simd_available": False,
        "flood_risk": "unknown",
        "flood_zone": None,
        "flood_available": False,
        "commute_user_min": None,
        "commute_user_route": None,
        "commute_partner_min": None,
        "commute_partner_route": None,
        "commute_available": False,
    }

    # Step 1: 坐标 + 数据区
    sys.stderr.write(f"📍 查询邮编: {postcode}\n")
    geo, err = get_postcode_data(postcode)
    if err:
        print(json.dumps({"error": err}, ensure_ascii=False))
        sys.exit(1)
    output.update({
        "lat": geo["lat"],
        "lon": geo["lon"],
        "data_zone": geo["data_zone"],
        "admin_district": geo["admin_district"],
    })
    sys.stderr.write(f"  → 坐标: ({geo['lat']}, {geo['lon']})，数据区: {geo['data_zone']}\n")

    # Step 2: SIMD
    if not args.skip_simd:
        sys.stderr.write("📊 查询 SIMD 数据...\n")
        simd = get_simd(geo["data_zone"])
        if simd:
            output.update({
                "simd_overall_rank": simd.get("overall_rank"),
                "simd_overall_decile": simd.get("overall_decile"),
                "simd_crime_decile": simd.get("crime_decile"),
                "simd_income_decile": simd.get("income_decile"),
                "simd_available": True,
            })
            sys.stderr.write(f"  → 综合分位: {simd.get('overall_decile')}/10，犯罪分位: {simd.get('crime_decile')}/10\n")
        else:
            sys.stderr.write("  ⚠️  SIMD 数据暂不可用\n")

    # Step 3: 洪水风险
    if not args.skip_flood:
        sys.stderr.write("🌊 查询洪水风险（SEPA）...\n")
        flood = get_flood_risk(geo["lat"], geo["lon"])
        output.update({
            "flood_risk": flood["risk"],
            "flood_zone": flood["zone"],
            "flood_available": flood["risk"] != "unknown",
        })
        sys.stderr.write(f"  → 洪水风险: {flood['risk']}\n")

    # Step 4: 通勤
    if not args.skip_commute:
        if not gmaps_key:
            sys.stderr.write("🚌 跳过通勤查询（未配置 GOOGLE_MAPS_API_KEY）\n")
        else:
            sys.stderr.write("🚌 查询通勤时间（Google Maps）...\n")
            commute = get_commute(geo["lat"], geo["lon"], postcode, prefs, gmaps_key)
            if commute:
                user_label = prefs.get("commute", {}).get("user_label", "你")
                partner_label = prefs.get("commute", {}).get("partner_label", "对象")
                output.update({
                    "commute_user_min": commute["user"]["min"],
                    "commute_user_route": commute["user"]["route"],
                    "commute_partner_min": commute["partner"]["min"],
                    "commute_partner_route": commute["partner"]["route"],
                    "commute_available": True,
                    "commute_user_label": user_label,
                    "commute_partner_label": partner_label,
                })
                sys.stderr.write(
                    f"  → {user_label}: {commute['user']['min']}min，"
                    f"{partner_label}: {commute['partner']['min']}min\n"
                )

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
