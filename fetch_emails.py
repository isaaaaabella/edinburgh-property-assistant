#!/usr/bin/env python3
"""
买房助手 - 邮件抓取器
供 /process-emails skill 使用，输出 JSON 到 stdout。
不做 AI 分析，只负责获取原始邮件数据。

用法:
  python fetch_emails.py              # 抓取最近48小时
  python fetch_emails.py --hours 72   # 抓取最近72小时
  python fetch_emails.py --reset      # 清空已处理记录后重新抓取
"""

import imaplib
import email
import json
import os
import sys
import argparse
from datetime import datetime, timedelta, timezone
from email.header import decode_header
import email.utils
import re

# ── 加载 .env ──────────────────────────────────────────────────────────
def load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    config = {}
    if not os.path.exists(env_path):
        print(json.dumps({"error": f".env not found at {env_path}"}))
        sys.exit(1)
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                config[key.strip()] = value.strip()
    return config

# ── 已处理记录 ─────────────────────────────────────────────────────────
PROCESSED_FILE = os.path.expanduser("~/.property_assistant_processed.json")

def load_processed():
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE) as f:
            return set(json.load(f))
    return set()

def reset_processed():
    with open(PROCESSED_FILE, "w") as f:
        json.dump([], f)

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
    return result.strip()

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
            if body:
                break  # 取第一个 text/plain 即可
    else:
        try:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
        except Exception:
            pass
    return body[:3000]

# ── 处理 PDF 附件 ─────────────────────────────────────────────────────
def save_pdf_attachments(msg):
    pdf_paths = []
    save_dir = os.path.expanduser("~/Documents/HomeReports")
    os.makedirs(save_dir, exist_ok=True)
    for part in msg.walk():
        if part.get_content_type() == "application/pdf":
            filename = decode_str(part.get_filename() or "attachment.pdf")
            # 安全化文件名
            filename = re.sub(r'[^\w\-_. ]', '_', filename)
            filepath = os.path.join(save_dir, filename)
            with open(filepath, "wb") as f:
                f.write(part.get_payload(decode=True))
            pdf_paths.append(filepath)
    return pdf_paths

# ── 处理转发邮件 ──────────────────────────────────────────────────────
def extract_forwarded(body, partner_name):
    """
    从转发邮件正文中提取原始发件人和原始内容。
    支持 Gmail 转发格式和 Outlook 转发格式。
    """
    original_sender = None
    original_body = body

    # Gmail 格式: "---------- Forwarded message ---------"
    gmail_marker = re.search(
        r'-{5,}\s*Forwarded message\s*-{5,}\s*\nFrom:\s*(.+)',
        body, re.IGNORECASE
    )
    if gmail_marker:
        original_sender = gmail_marker.group(1).strip()
        original_body = body[gmail_marker.start():]
        # 去掉 header 行，保留正文
        lines = original_body.split('\n')
        content_start = 0
        for i, line in enumerate(lines):
            if i > 0 and line.strip() == '':
                content_start = i + 1
                break
        original_body = '\n'.join(lines[content_start:]).strip()
        return original_sender, original_body

    # Outlook 格式: "From: xxx\nSent: xxx\nTo: xxx\nSubject: xxx"
    outlook_marker = re.search(
        r'From:\s*(.+)\nSent:',
        body, re.IGNORECASE
    )
    if outlook_marker:
        original_sender = outlook_marker.group(1).strip()
        original_body = body[outlook_marker.start():]
        return original_sender, original_body

    # Apple Mail / 简单转发: "> On ... wrote:"
    apple_marker = re.search(
        r'On .+wrote:\s*\n(.+)',
        body, re.IGNORECASE | re.DOTALL
    )
    if apple_marker:
        original_body = apple_marker.group(1).strip()
        return None, original_body

    return original_sender, original_body

# ── 相关性过滤 ────────────────────────────────────────────────────────
RELEVANT_KEYWORDS = [
    "property", "viewing", "home report", "closing date",
    "offer", "solicitor", "mortgage", "flat", "apartment",
    "rightmove", "espc", "warners", "mcdougall", "shepherd",
    "savills", "knight frank", "purplebricks", "citylets",
    "edinburgh", "tenement", "survey", "conveyancing",
    "missives", "settlement", "in principle", "aip",
    "note of interest", "fixed price", "offers over",
]

def is_relevant(subject, sender, body):
    text = f"{subject} {sender} {body[:500]}".lower()
    return any(k in text for k in RELEVANT_KEYWORDS)

# ── 主逻辑 ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=48, help="抓取最近N小时的邮件")
    parser.add_argument("--reset", action="store_true", help="清空已处理记录")
    args = parser.parse_args()

    config = load_env()

    gmail_user = config.get("GMAIL_USER", "")
    gmail_password = config.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    partner_email = config.get("PARTNER_EMAIL", "").lower().strip()
    partner_name = config.get("PARTNER_NAME", "伴侣")

    if not gmail_user or not gmail_password:
        print(json.dumps({"error": "GMAIL_USER 或 GMAIL_APP_PASSWORD 未配置"}))
        sys.exit(1)

    if args.reset:
        reset_processed()
        sys.stderr.write("✅ 已清空处理记录\n")

    processed_ids = load_processed()

    # 计算时间截止点
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(gmail_user, gmail_password)
        mail.select("INBOX")
    except Exception as e:
        print(json.dumps({"error": f"Gmail连接失败: {str(e)}"}))
        sys.exit(1)

    # 搜索 INBOX 中的所有邮件（最新100封）
    _, messages = mail.search(None, "ALL")
    all_ids = messages[0].split()
    recent_ids = all_ids[-100:] if len(all_ids) > 100 else all_ids

    results = []

    for msg_id in reversed(recent_ids):
        msg_id_str = msg_id.decode()

        if msg_id_str in processed_ids:
            continue

        try:
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
        except Exception:
            continue

        msg = email.message_from_bytes(raw)
        subject = decode_str(msg.get("Subject", ""))
        sender = decode_str(msg.get("From", ""))

        # 解析日期
        date_tuple = email.utils.parsedate_to_datetime(msg.get("Date", "")) if msg.get("Date") else None
        if date_tuple:
            # 确保 timezone-aware
            if date_tuple.tzinfo is None:
                date_tuple = date_tuple.replace(tzinfo=timezone.utc)
            if date_tuple < cutoff:
                continue  # 超出时间范围
            date_str = date_tuple.strftime("%Y-%m-%d")
            datetime_str = date_tuple.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            date_str = datetime.now().strftime("%Y-%m-%d")
            datetime_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        body = get_email_body(msg)
        pdf_paths = save_pdf_attachments(msg)

        # 检测是否为转发邮件
        sender_email = re.search(r'<(.+?)>', sender)
        sender_email_str = sender_email.group(1).lower() if sender_email else sender.lower()

        is_forwarded = False
        original_sender = None
        forwarded_by = None

        subject_lower = subject.lower().strip()
        is_fwd_prefix = subject_lower.startswith("fwd:") or subject_lower.startswith("fw:")

        if partner_email and sender_email_str == partner_email:
            is_forwarded = True
            forwarded_by = partner_name
            if is_fwd_prefix:
                # 清理主题的 Fwd:/FW: 前缀
                subject = re.sub(r'^(fwd?:\s*)+', '', subject, flags=re.IGNORECASE).strip()
            orig_sender, orig_body = extract_forwarded(body, partner_name)
            if orig_sender:
                original_sender = orig_sender
                body = orig_body  # 使用原始正文做分析
        elif is_fwd_prefix:
            # 自己转发的邮件（非 partner）
            is_forwarded = True
            subject = re.sub(r'^(fwd?:\s*)+', '', subject, flags=re.IGNORECASE).strip()
            orig_sender, orig_body = extract_forwarded(body, "")
            if orig_sender:
                original_sender = orig_sender
                body = orig_body

        # 过滤不相关邮件
        effective_sender = original_sender or sender
        if not is_relevant(subject, effective_sender, body):
            continue

        results.append({
            "id": msg_id_str,
            "subject": subject,
            "sender": sender,
            "sender_email": sender_email_str,
            "date": date_str,
            "datetime": datetime_str,
            "body": body[:2500],
            "has_pdf": len(pdf_paths) > 0,
            "pdf_paths": pdf_paths,
            "is_forwarded": is_forwarded,
            "forwarded_by": forwarded_by,
            "original_sender": original_sender,
        })

    mail.logout()

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
