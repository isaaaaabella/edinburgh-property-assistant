#!/usr/bin/env python3
"""
买房助手 - 新房源一键录入
用法: python add_property.py <rightmove_url>
"""

import sys
import re
import json
import requests
from bs4 import BeautifulSoup

import config
NOTION_HEADERS = config.NOTION_HEADERS
PROPERTY_DB_ID = config.NOTION_PROPERTY_DB

SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}

# ── 抓取Rightmove页面 ─────────────────────────────────────────────────
def scrape_rightmove(url):
    print(f"🔍 正在抓取：{url}")
    r = requests.get(url, headers=SCRAPE_HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    data = {}

    # 图片（og:image）
    og_image = soup.find("meta", property="og:image")
    if og_image:
        data["image_url"] = og_image.get("content", "")

    # 标题
    og_title = soup.find("meta", property="og:title")
    if og_title:
        data["title"] = og_title.get("content", "")

    # 从JSON-LD或页面脚本提取结构化数据
    scripts = soup.find_all("script", type="application/ld+json")
    for s in scripts:
        try:
            ld = json.loads(s.string)
            if isinstance(ld, dict) and ld.get("@type") == "Residence":
                data["address"] = ld.get("name", "")
                data["description"] = ld.get("description", "")
        except Exception:
            pass

    # 从页面内嵌JS数据提取更多信息
    page_text = r.text

    # 挂牌价
    price_match = re.search(r'"price":\s*(\d+)', page_text)
    if price_match:
        data["price"] = int(price_match.group(1))

    # 卧室数
    bed_match = re.search(r'"bedrooms":\s*(\d+)', page_text)
    if bed_match:
        data["bedrooms"] = int(bed_match.group(1))

    # 面积
    size_match = re.search(r'"floorplanArea":\s*\{[^}]*"min":\s*([\d.]+)', page_text)
    if size_match:
        data["floor_area"] = float(size_match.group(1))

    # 地址（从title提取）
    if not data.get("address") and data.get("title"):
        # title格式通常是 "2 bedroom flat for sale in 地址"
        addr_match = re.search(r"for sale in (.+?)(?:\||$)", data["title"])
        if addr_match:
            data["address"] = addr_match.group(1).strip()

    # 楼层信息（从描述推断）
    desc_lower = page_text.lower()
    if "ground floor" in desc_lower:
        data["floor"] = "Ground ⚠️"
    elif "top floor" in desc_lower or "third floor" in desc_lower:
        data["floor"] = "顶层 ⚠️"
    elif "second floor" in desc_lower:
        data["floor"] = "2F ✅"
    elif "first floor" in desc_lower:
        data["floor"] = "1F"

    # 建筑类型
    if "tenement" in desc_lower:
        data["building_type"] = "维多利亚Tenement ✅"
    elif "purpose built" in desc_lower or "modern" in desc_lower:
        data["building_type"] = "现代公寓 ⚠️"

    return data

# ── 创建Notion页面 ────────────────────────────────────────────────────
def create_notion_page(data, url):
    address = data.get("address", "新房源")
    print(f"\n📝 正在创建Notion页面：{address}")

    props = {
        "地址": {"title": [{"type": "text", "text": {"content": address}}]},
        "状态": {"select": {"name": "🔍 待看"}},
        "Rightmove链接": {"url": url},
    }

    if data.get("price"):
        props["挂牌价(£)"] = {"number": data["price"]}
    if data.get("bedrooms"):
        props["卧室数"] = {"number": data["bedrooms"]}
    if data.get("floor_area"):
        props["面积(m²)"] = {"number": data["floor_area"]}
    if data.get("floor"):
        props["楼层"] = {"select": {"name": data["floor"]}}
    if data.get("building_type"):
        props["建筑类型"] = {"select": {"name": data["building_type"]}}

    page_data = {
        "parent": {"database_id": PROPERTY_DB_ID},
        "properties": props,
    }

    # 封面图片
    if data.get("image_url"):
        page_data["cover"] = {
            "type": "external",
            "external": {"url": data["image_url"]}
        }

    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=page_data
    )
    r.raise_for_status()
    page_id = r.json()["id"]

    # 添加页面模板内容
    blocks = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"type": "text", "text": {"content": "👀 看房记录"}}]}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"type": "text", "text": {"content": "你的感受"}}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"type": "text", "text": {"content": "（看完后填写）"},
                                      "annotations": {"color": "gray"}}]}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"type": "text", "text": {"content": "伴侣的感受"}}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"type": "text", "text": {"content": "（看完后填写）"},
                                      "annotations": {"color": "gray"}}]}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"type": "text", "text": {"content": "中介问答"}}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"type": "text", "text": {"content": "（看完后填写）"},
                                      "annotations": {"color": "gray"}}]}},
        {"object": "block", "type": "divider", "divider": {}},
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"type": "text", "text": {"content": "✅❌ 优缺点"}}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"type": "text", "text": {"content": "优点："}}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"type": "text", "text": {"content": "缺点："}}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"type": "text", "text": {"content": "待确认："}}]}},
        {"object": "block", "type": "divider", "divider": {}},
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"type": "text", "text": {"content": "📄 Home Report分析"}}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"type": "text", "text": {"content": "（运行 home_report_parser.py 后自动填入）"},
                                      "annotations": {"color": "gray"}}]}},
    ]

    requests.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=NOTION_HEADERS,
        json={"children": blocks}
    )

    return page_id

# ── 主流程 ────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print("用法: python add_property.py <rightmove_url>")
        print("例如: python add_property.py https://www.rightmove.co.uk/properties/174120746")
        return

    url = sys.argv[1].split("#")[0]  # 去掉#后面的部分

    # 抓取数据
    data = scrape_rightmove(url)

    # 显示抓取结果
    print(f"\n✅ 抓取完成：")
    print(f"   地址：{data.get('address', '未识别')}")
    print(f"   挂牌价：£{data.get('price', '未识别'):,}" if data.get('price') else "   挂牌价：未识别")
    print(f"   卧室：{data.get('bedrooms', '未识别')}")
    print(f"   面积：{data.get('floor_area', '未识别')}m²")
    print(f"   楼层：{data.get('floor', '未识别')}")
    print(f"   图片：{'✅ 已找到' if data.get('image_url') else '❌ 未找到'}")

    # 确认
    confirm = input("\n确认创建Notion页面？(y/n): ").strip().lower()
    if confirm != 'y':
        print("已取消")
        return

    # 创建页面
    page_id = create_notion_page(data, url)
    print(f"\n🎉 完成！Notion页面已创建")
    print(f"💡 提示：接下来可以运行 home_report_parser.py 解析Home Report")

if __name__ == "__main__":
    main()
