#!/usr/bin/env python3
"""
买房助手 - 邮件监听服务
用法: python email_monitor.py
每5分钟检查一次Gmail，自动更新Notion并发送Telegram通知
"""

import imaplib
import email
import time
import json
import re
import os
import requests
from email.header import decode_header
from datetime import datetime, timezone
import email.utils

import config

CONFIG = {
    "gmail_user": config.GMAIL_USER,
    "gmail_app_password": config.GMAIL_PASSWORD,
    "anthropic_key": config.ANTHROPIC_KEY,
    "telegram_token": config.TELEGRAM_TOKEN,
    "telegram_chat_id": config.TELEGRAM_CHAT_ID,
    "check_interval": config.CHECK_INTERVAL,
}

NOTION_HEADERS = config.NOTION_HEADERS
PROPERTY_DB_ID = config.NOTION_PROPERTY_DB
EMAIL_DB_ID = config.NOTION_EMAIL_DB

# 记录已处理的邮件ID，避免重复处理
PROCESSED_FILE = os.path.expanduser("~/.property_assistant_processed.json")

def load_processed():
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE) as f:
            return set(json.load(f))
    return set()

def save_processed(processed):
    with open(PROCESSED_FILE, "w") as f:
        json.dump(list(processed), f)

# ── Telegram通知 ──────────────────────────────────────────────────────
def send_telegram(message):
    url = f"https://api.telegram.org/bot{CONFIG['telegram_token']}/sendMessage"
    payload = {
        "chat_id": CONFIG["telegram_chat_id"],
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"  ⚠️ Telegram发送失败: {e}")
        return False

# ── Gmail连接 ─────────────────────────────────────────────────────────
def connect_gmail():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(CONFIG["gmail_user"], CONFIG["gmail_app_password"].replace(" ", ""))
    return mail

# ── 解码邮件头部 ──────────────────────────────────────────────────────
def decode_str(s):
    if not s:
        return ""
    parts = decode_header(s)
    result = ""
    for part, encoding in parts:
        if isinstance(part, bytes):
            try:
                result += part.decode(encoding or "utf-8", errors="replace")
            except Exception:
                result += part.decode("utf-8", errors="replace")
        else:
            result += str(part)
    return result

# ── 提取邮件正文 ──────────────────────────────────────────────────────
def get_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body += part.get_payload(decode=True).decode("utf-8", errors="replace")
                except Exception:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
        except Exception:
            pass
    return body[:3000]  # 限制长度

# ── 检查是否有PDF附件 ─────────────────────────────────────────────────
def get_pdf_attachments(msg):
    pdfs = []
    for part in msg.walk():
        if part.get_content_type() == "application/pdf":
            filename = decode_str(part.get_filename() or "attachment.pdf")
            data = part.get_payload(decode=True)
            pdfs.append({"filename": filename, "data": data})
    return pdfs

# ── 用Claude分析邮件 ──────────────────────────────────────────────────
def analyze_email_with_claude(subject, sender, body):
    if not CONFIG["anthropic_key"]:
        return None

    prompt = f"""分析这封买房相关的邮件，返回JSON格式，不要有其他文字：

{{
  "email_type": "viewing_confirmation|closing_date|home_report|offer_update|general_agent|mortgage|solicitor|other",
  "property_address": "提取到的房产地址，没有则null",
  "viewing_date": "看房日期 YYYY-MM-DD，没有则null",
  "viewing_time": "看房时间 HH:MM，没有则null",
  "closing_date": "截止日期 YYYY-MM-DD，没有则null",
  "is_agent_reply": true或false,
  "summary_cn": "用中文一句话总结邮件内容",
  "action_required": true或false,
  "action_description": "需要做什么，没有则null"
}}

发件人: {sender}
主题: {subject}
内容: {body[:1500]}"""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CONFIG["anthropic_key"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        r.raise_for_status()
        content = r.json()["content"][0]["text"].strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content.strip())
    except Exception as e:
        print(f"  ⚠️ Claude分析失败: {e}")
        return None

# ── 获取所有房源 ──────────────────────────────────────────────────────
def get_all_properties():
    url = f"https://api.notion.com/v1/databases/{PROPERTY_DB_ID}/query"
    r = requests.post(url, headers=NOTION_HEADERS, json={})
    r.raise_for_status()
    props = []
    for p in r.json()["results"]:
        title = p["properties"]["地址"]["title"]
        name = title[0]["plain_text"] if title else ""
        props.append({"id": p["id"], "name": name})
    return props

# ── 匹配房源 ──────────────────────────────────────────────────────────
def match_property(address, properties):
    if not address:
        return None
    address_lower = address.lower()
    for p in properties:
        # 提取门牌号和街道名做模糊匹配
        keywords = [w for w in p["name"].lower().split() if len(w) > 3]
        matches = sum(1 for k in keywords if k in address_lower)
        if matches >= 2:
            return p
    return None

# ── 更新Notion房源状态 ────────────────────────────────────────────────
def update_property_status(page_id, analysis):
    props = {}

    # 根据邮件类型更新状态
    email_type = analysis.get("email_type", "")
    if analysis.get("is_agent_reply"):
        props["状态"] = {"select": {"name": "⭐ 感兴趣"}}

    if analysis.get("viewing_date"):
        date_str = analysis["viewing_date"]
        if analysis.get("viewing_time"):
            props["Viewing时间"] = {"date": {"start": f"{date_str}T{analysis['viewing_time']}:00"}}
        else:
            props["Viewing时间"] = {"date": {"start": date_str}}
        props["状态"] = {"select": {"name": "👀 已看"}}

    if analysis.get("closing_date"):
        props["Closing Date"] = {"date": {"start": analysis["closing_date"]}}

    if props:
        requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=NOTION_HEADERS,
            json={"properties": props}
        )

# ── 创建邮件归档记录 ──────────────────────────────────────────────────
def archive_email(subject, sender, date_str, analysis, property_id=None):
    type_map = {
        "viewing_confirmation": "📅 Viewing确认",
        "closing_date": "⚠️ Closing Date",
        "home_report": "📄 Home Report",
        "mortgage": "🏦 Mortgage/银行",
        "solicitor": "⚖️ Solicitor",
        "general_agent": "📅 Viewing确认",
        "other": "其他"
    }

    email_type = analysis.get("email_type", "other") if analysis else "other"
    notion_type = type_map.get(email_type, "其他")
    summary = analysis.get("summary_cn", subject) if analysis else subject

    props = {
        "主题": {"title": [{"type": "text", "text": {"content": subject[:100]}}]},
        "类型": {"select": {"name": notion_type}},
        "收件日期": {"date": {"start": date_str}},
        "发件人": {"rich_text": [{"type": "text", "text": {"content": sender[:200]}}]},
        "摘要": {"rich_text": [{"type": "text", "text": {"content": summary[:500]}}]},
        "需要行动": {"checkbox": analysis.get("action_required", False) if analysis else False},
    }

    if property_id:
        props["关联房源"] = {"relation": [{"id": property_id}]}

    if analysis and analysis.get("action_description"):
        deadline = analysis.get("closing_date") or analysis.get("viewing_date")
        if deadline:
            props["行动截止"] = {"date": {"start": deadline}}

    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json={"parent": {"database_id": EMAIL_DB_ID}, "properties": props}
    )
    r.raise_for_status()

# ── 保存PDF到本地 ─────────────────────────────────────────────────────
def save_pdf(pdf_data, filename):
    save_dir = os.path.expanduser("~/Documents/HomeReports")
    os.makedirs(save_dir, exist_ok=True)
    filepath = os.path.join(save_dir, filename)
    with open(filepath, "wb") as f:
        f.write(pdf_data)
    return filepath

# ── 处理单封邮件 ──────────────────────────────────────────────────────
def process_email(msg_data, properties):
    msg = email.message_from_bytes(msg_data)
    subject = decode_str(msg.get("Subject", ""))
    sender = decode_str(msg.get("From", ""))
    date_tuple = email.utils.parsedate(msg.get("Date", ""))
    date_str = datetime(*date_tuple[:6]).strftime("%Y-%m-%d") if date_tuple else datetime.now().strftime("%Y-%m-%d")

    print(f"\n  📧 处理邮件：{subject[:60]}")
    print(f"     来自：{sender[:50]}")

    body = get_email_body(msg)
    pdfs = get_pdf_attachments(msg)

    # Claude分析
    analysis = analyze_email_with_claude(subject, sender, body)

    # 匹配房源
    matched_property = None
    if analysis and analysis.get("property_address"):
        matched_property = match_property(analysis["property_address"], properties)
    if not matched_property:
        # 尝试从邮件内容直接匹配
        for p in properties:
            keywords = [w for w in p["name"].split() if len(w) > 3]
            if any(k.lower() in body.lower() or k.lower() in subject.lower() for k in keywords):
                matched_property = p
                break

    property_id = matched_property["id"] if matched_property else None
    property_name = matched_property["name"] if matched_property else "未匹配到房源"

    # 更新Notion
    if property_id and analysis:
        update_property_status(property_id, analysis)

    # 归档邮件
    try:
        archive_email(subject, sender, date_str, analysis, property_id)
    except Exception as e:
        print(f"  ⚠️ 邮件归档失败: {e}")

    # 处理PDF附件
    pdf_notifications = []
    for pdf in pdfs:
        filepath = save_pdf(pdf["data"], pdf["filename"])
        pdf_notifications.append(filepath)
        print(f"  📄 PDF已保存: {filepath}")

    # 构建Telegram通知
    emoji_map = {
        "viewing_confirmation": "📅",
        "closing_date": "🚨",
        "home_report": "📄",
        "mortgage": "🏦",
        "solicitor": "⚖️",
        "general_agent": "🏠",
        "other": "📬"
    }

    if analysis:
        email_type = analysis.get("email_type", "other")
        emoji = emoji_map.get(email_type, "📬")
        summary = analysis.get("summary_cn", subject)

        msg_lines = [
            f"{emoji} <b>新邮件</b>",
            f"📍 {property_name}",
            f"📝 {summary}",
        ]

        if analysis.get("viewing_date"):
            msg_lines.append(f"👀 看房时间：{analysis['viewing_date']} {analysis.get('viewing_time', '')}")
        if analysis.get("closing_date"):
            msg_lines.append(f"🚨 Closing Date：{analysis['closing_date']}")
        if analysis.get("action_required") and analysis.get("action_description"):
            msg_lines.append(f"⚡ 需要行动：{analysis['action_description']}")
        if pdf_notifications:
            msg_lines.append(f"📄 Home Report已保存到Documents/HomeReports/")
            msg_lines.append(f"💡 运行: python home_report_parser.py \"{pdf_notifications[0]}\"")

        send_telegram("\n".join(msg_lines))
    else:
        send_telegram(f"📬 新邮件\n📍 {property_name}\n主题：{subject[:80]}")

    return True

# ── 主检查循环 ────────────────────────────────────────────────────────
def check_emails(processed_ids, properties):
    new_count = 0
    try:
        mail = connect_gmail()
        mail.select("INBOX")

        # 搜索最近7天的未读邮件（或所有相关邮件）
        _, messages = mail.search(None, "ALL")
        all_ids = messages[0].split()

        # 只处理最新的50封
        recent_ids = all_ids[-50:] if len(all_ids) > 50 else all_ids

        for msg_id in reversed(recent_ids):
            msg_id_str = msg_id.decode()
            if msg_id_str in processed_ids:
                continue

            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            if msg_data and msg_data[0]:
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                sender = decode_str(msg.get("From", "")).lower()

                # 只处理可能是中介/相关的邮件
                # 关键词过滤（可以根据实际情况调整）
                subject = decode_str(msg.get("Subject", "")).lower()
                relevant_keywords = [
                    "property", "viewing", "home report", "closing date",
                    "offer", "solicitor", "mortgage", "flat", "apartment",
                    "rightmove", "espc", "warners", "mcdougall", "shepherd",
                    "buccleuch", "cowan", "gray street", "edinburgh"
                ]

                is_relevant = any(k in subject or k in sender for k in relevant_keywords)

                if is_relevant:
                    process_email(raw, properties)
                    new_count += 1

                processed_ids.add(msg_id_str)

        mail.logout()

    except Exception as e:
        print(f"  ❌ 邮件检查失败: {e}")
        send_telegram(f"⚠️ 买房助手邮件检查出错：{str(e)[:100]}")

    return new_count

# ── 入口 ──────────────────────────────────────────────────────────────
def main():
    print("🏠 买房助手 - 邮件监听服务启动")

    # Anthropic Key从.env加载
    if not CONFIG["anthropic_key"]:
        print("⚠️  未配置ANTHROPIC_API_KEY，将跳过AI分析")

    # 测试Telegram
    print("\n📱 测试Telegram连接...")
    if send_telegram("🏠 买房助手已启动！\n将每5分钟检查一次邮箱。"):
        print("✅ Telegram连接成功")
    else:
        print("⚠️ Telegram连接失败，请检查配置")

    # 加载已处理记录
    processed_ids = load_processed()
    print(f"📋 已有 {len(processed_ids)} 封邮件记录")

    # 获取房源列表
    properties = get_all_properties()
    print(f"🏠 Notion中有 {len(properties)} 套房源")
    for p in properties:
        print(f"   - {p['name']}")

    print(f"\n⏰ 开始监听，每{CONFIG['check_interval']//60}分钟检查一次")
    print("按 Ctrl+C 停止\n")

    # 主循环
    while True:
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] 检查邮件...")

        new_count = check_emails(processed_ids, properties)
        save_processed(processed_ids)

        if new_count > 0:
            print(f"  ✅ 处理了 {new_count} 封新邮件")
        else:
            print(f"  💤 没有新的相关邮件")

        # 每小时刷新房源列表
        properties = get_all_properties()

        time.sleep(CONFIG["check_interval"])

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 监听服务已停止")
