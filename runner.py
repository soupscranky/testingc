#!/usr/bin/env python3
"""Automated web form interaction tool."""

import base64
import csv
import io
import email as email_lib
import imaplib
import json
import os
import random
import re
import string
import sys
import time
import urllib.request

from camoufox.sync_api import Camoufox

FORM_URL        = os.environ.get("FORM_URL", "")
TASKS_B64       = os.environ.get("TASKS_B64", "")
IMAP_USER       = os.environ.get("IMAP_USER", "")
IMAP_PASS       = os.environ.get("IMAP_PASS", "")
PROXY_TEMPLATE  = os.environ.get("PROXY_TEMPLATE", "")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
START_TASK      = os.environ.get("START_TASK", "").strip()
STATE_FILE      = "state.txt"
OFFSET_FILE     = "offset.txt"


def mask(value):
    if not value or len(value) <= 2:
        return "***"
    return value[:2] + "***"


def load_tasks():
    if TASKS_B64:
        raw = base64.b64decode(TASKS_B64).decode("utf-8")
        return list(csv.DictReader(io.StringIO(raw)))
    if os.path.exists("tasks.csv"):
        with open("tasks.csv", newline="") as f:
            return list(csv.DictReader(f))
    print("No tasks found. Set TASKS_B64 or create tasks.csv.")
    sys.exit(1)


def get_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return int(f.read().strip() or "0")
    return 0


def save_state(val):
    with open(STATE_FILE, "w") as f:
        f.write(str(val))


def get_offset():
    if os.path.exists(OFFSET_FILE):
        with open(OFFSET_FILE) as f:
            return int(f.read().strip() or "0")
    return 0


def save_offset(val):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(val))


def make_proxy():
    if not PROXY_TEMPLATE:
        return None
    session = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    raw = PROXY_TEMPLATE.replace("{SESSION}", session)
    parts = raw.split(":")
    if len(parts) == 4:
        host, port, user, pw = parts
        return {"server": f"http://{host}:{port}", "username": user, "password": pw}
    if "@" in raw:
        creds, hostport = raw.rsplit("@", 1)
        user, pw = creds.split(":", 1)
        return {"server": f"http://{hostport}", "username": user, "password": pw}
    return {"server": f"http://{raw}"}


def notify(task_idx, status, email_addr=""):
    if not DISCORD_WEBHOOK:
        return
    colors = {"success": 65280, "failed": 16711680, "blocked": 16753920}
    color = colors.get(status, 8421504)
    payload = {
        "embeds": [{
            "color": color,
            "fields": [
                {"name": "Task", "value": str(task_idx), "inline": True},
                {"name": "Status", "value": status, "inline": True},
                {"name": "Account", "value": mask(email_addr), "inline": True},
            ],
        }]
    }
    try:
        req = urllib.request.Request(
            DISCORD_WEBHOOK,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def fetch_code(target_email, timeout=180, poll_interval=10):
    code_regex = re.compile(r"verification\s+code.*?\b(\d{6})\b", re.IGNORECASE | re.DOTALL)
    target_email = target_email.lower()
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        conn = None
        try:
            conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            conn.login(IMAP_USER, IMAP_PASS)
            conn.select("INBOX")
            status, data = conn.search(None, '(SUBJECT "Verification Code")')
            if status != "OK":
                raise RuntimeError("search failed")
            for msg_id in reversed(data[0].split()):
                status, msg_data = conn.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data:
                    continue
                raw_email = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw_email)
                recipient_headers = " ".join([msg.get("To", ""), msg.get("X-ICLOUD-HME", "")]).lower()
                if target_email not in recipient_headers:
                    continue
                body = _extract_body(msg)
                if not body:
                    continue
                match = code_regex.search(body)
                if not match:
                    continue
                code = match.group(1)
                conn.store(msg_id, "+FLAGS", "\\Seen")
                return code
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_interval, remaining))
    return None


def _extract_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")
    return None


def human_pause(min_s=0.6, max_s=1.8):
    time.sleep(random.uniform(min_s, max_s))


def is_present(page, selector):
    try:
        return page.locator(selector).count() > 0
    except Exception:
        return False


def is_visible(page, selector):
    try:
        return page.locator(selector).is_visible()
    except Exception:
        return False


def safe_click(page, selector):
    try:
        page.dispatch_event(selector, 'click')
        return True
    except Exception:
        pass
    try:
        page.click(selector, timeout=5000)
        return True
    except Exception:
        return False


def fill_field(page, label, selectors, value):
    for sel in selectors:
        if is_present(page, sel):
            try:
                page.fill(sel, value, timeout=8000)
                time.sleep(0.3)
                actual = page.input_value(sel)
                if actual and actual.strip() == str(value).strip():
                    print(f"  [{label}] filled")
                    return True
            except Exception:
                pass
    print(f"  [{label}] JS fallback")
    for sel in selectors:
        if is_present(page, sel):
            try:
                page.evaluate(f'''
                    (function(){{
                        var el = document.querySelector('{sel}');
                        if (el) {{
                            el.focus();
                            el.value = '{value}';
                            el.dispatchEvent(new Event('input', {{bubbles: true}}));
                            el.dispatchEvent(new Event('change', {{bubbles: true}}));
                        }}
                    }})()
                ''')
                time.sleep(0.3)
                actual = page.input_value(sel)
                if actual and actual.strip() == str(value).strip():
                    print(f"  [{label}] filled via JS")
                    return True
            except Exception:
                pass
    print(f"  [{label}] could not be filled")
    return False


def dismiss_cookie_banner(page):
    for sel in ['#cmpwelcomebtnno > a', '#onetrust-reject-all-handler', '#onetrust-accept-btn-handler']:
        if is_visible(page, sel):
            safe_click(page, sel)
            time.sleep(1)


def wait_for_profile_page(page, timeout=40):
    for _ in range(timeout):
        if is_present(page, 'select[name^="additionalCustomerAttributes-1_"]') or \
           is_present(page, 'ev-pl-button[data-qa="save-data-button"]'):
            return True
        time.sleep(1)
    return False


def wait_through_queue(page, max_wait_minutes=90):
    print("[Queue] Waiting …")
    start = time.time()
    max_wait = max_wait_minutes * 60
    last_log_time = 0

    while time.time() - start < max_wait:
        elapsed = int(time.time() - start)

        try:
            body_text = page.evaluate('(document.body || {}).innerText || ""') or ""
            if "access has been restricted" in body_text.lower() or "error403" in body_text.lower():
                print("blocked")
                return "blocked"
        except Exception:
            pass

        dismiss_cookie_banner(page)

        try:
            current_url = page.url
        except Exception:
            time.sleep(5)
            continue

        if "softblock" in current_url.lower():
            print("[Queue] Softblock detected")
            return "blocked"

        if "queue" not in current_url.lower():
            if ("la28id" in current_url or "login" in current_url
                    or "register" in current_url or "mycustomerdata" in current_url):
                m, s = divmod(elapsed, 60)
                print(f"[Queue] Passed after {m}m {s}s")
                return True
            try:
                if (is_present(page, 'a[href*="../register/"]')
                        or is_present(page, '.gigya-input-fauxbutton')
                        or is_present(page, '#register-site-login')
                        or is_present(page, '#gigya-textbox-code')):
                    m, s = divmod(elapsed, 60)
                    print(f"[Queue] Passed after {m}m {s}s")
                    return True
            except Exception:
                pass

        if is_visible(page, 'button.botdetect-button'):
            print("[Queue] Clicking JOIN …")
            safe_click(page, 'button.botdetect-button')
            time.sleep(4)
            continue

        if is_visible(page, '#buttonConfirmVisitorPresence'):
            safe_click(page, '#buttonConfirmVisitorPresence')
            time.sleep(1)
            continue

        try:
            clicked = page.evaluate("""
                (function(){
                    var buttons = document.querySelectorAll('button, a, input[type="submit"]');
                    for (var i = 0; i < buttons.length; i++) {
                        var el = buttons[i];
                        var text = (el.textContent || el.value || '').trim().toLowerCase();
                        if (text === 'continue' || text === 'weiter') {
                            var style = window.getComputedStyle(el);
                            if (style.display !== 'none' && style.visibility !== 'hidden' && el.offsetWidth > 0) {
                                el.click();
                                return true;
                            }
                        }
                    }
                    return false;
                })()
            """)
            if clicked:
                print(f"[Queue] Clicked Continue at {elapsed}s")
                time.sleep(4)
                continue
        except Exception:
            pass

        if elapsed - last_log_time >= 30:
            last_log_time = elapsed
            m, s = divmod(elapsed, 60)
            print(f"[Queue] Still waiting … {m}m {s}s")

        time.sleep(3)

    print("[Queue] Timed out")
    return False


def enter_verification_code(page, code):
    selector = "#gigya-textbox-code"
    for _ in range(15):
        if is_present(page, selector):
            break
        time.sleep(1)

    for method in range(1, 4):
        try:
            if method == 1:
                page.dispatch_event(selector, 'click')
                time.sleep(0.3)
                page.fill(selector, "", timeout=5000)
                time.sleep(0.1)
                page.type(selector, code, delay=50)
                time.sleep(0.5)
            elif method == 2:
                page.evaluate(f"""
                    (function(){{
                        var el = document.querySelector('{selector}');
                        if (!el) return;
                        el.focus(); el.click(); el.value = '';
                        var code = '{code}';
                        for (var i = 0; i < code.length; i++) {{
                            el.value += code[i];
                            el.dispatchEvent(new Event('input', {{bubbles: true}}));
                            el.dispatchEvent(new KeyboardEvent('keydown', {{key: code[i], bubbles: true}}));
                            el.dispatchEvent(new KeyboardEvent('keyup', {{key: code[i], bubbles: true}}));
                        }}
                        el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }})()
                """)
                time.sleep(0.5)
            elif method == 3:
                page.evaluate(f"""
                    (function(){{
                        var el = document.querySelector('{selector}');
                        if (!el) return;
                        el.focus(); el.value = '{code}';
                        el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        el.dispatchEvent(new Event('change', {{bubbles: true}}));
                        el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles: true}}));
                    }})()
                """)
                time.sleep(0.5)

            actual = page.input_value(selector)
            if str(actual).strip() == str(code).strip():
                return True
        except Exception:
            pass

    return False


def process_task(task, proxy_dict=None):

    print(f"\n{'='*50}")
    print(f"  {mask(task.get('email', ''))} | {mask(task.get('first_name', ''))} {mask(task.get('last_name', ''))}")
    print(f"{'='*50}")

    email_addr = task.get("email", "")
    password = task.get("password", "")
    first = task.get("first_name", "")
    last = task.get("last_name", "")
    country = task.get("country", "")
    zip_code = task.get("zip_code", "")

    cfox_kwargs = {
        "humanize": True,
        "headless": True,
        "fingerprint_preset": True,
        "os": "macos",
    }
    if proxy_dict:
        cfox_kwargs["proxy"] = proxy_dict
        cfox_kwargs["geoip"] = True
        print(f"  Proxy: {mask(proxy_dict.get('server', ''))}")

    try:
        with Camoufox(**cfox_kwargs) as browser:
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            page.goto(FORM_URL, wait_until="domcontentloaded")
            time.sleep(4)

            dismiss_cookie_banner(page)

            current_url = page.url
            if "queue" in current_url.lower() or "enqueuetoken" in current_url.lower():
                passed = wait_through_queue(page)
                if passed == "blocked":
                    return "blocked"
                if not passed:
                    return False
                time.sleep(5)
            else:
                print("[Queue] No queue detected.")

            dismiss_cookie_banner(page)

            print("[Form] Waiting for login page …")
            for _ in range(30):
                if (is_present(page, 'a[href*="../register/"]')
                        or is_present(page, '.gigya-input-fauxbutton')
                        or is_present(page, '#register-site-login')):
                    break
                time.sleep(2)

            print("[Form] Clicking register …")
            if not safe_click(page, 'a[href*="../register/"]'):
                safe_click(page, '.gigya-input-fauxbutton')
            time.sleep(5)

            print("[Form] Filling form …")
            try:
                page.wait_for_selector('#register-site-login', timeout=30000)
            except Exception:
                print("  [Form] Registration form not found")
                return False
            time.sleep(3)

            fill_field(page, "Email", [
                '#register-site-login input[name="email"]',
                '#register-site-login input[type="email"]',
                '#register-site-login > div:nth-child(1) > div.gigya-layout-row > div > input',
            ], email_addr)
            human_pause(0.5, 1.0)

            fill_field(page, "First name", [
                '#register-site-login input[name="profile.firstName"]',
                '#register-site-login input[name="firstName"]',
                '#register-site-login input[placeholder*="First"]',
            ], first)
            human_pause(0.5, 1.0)

            fill_field(page, "Last name", [
                '#register-site-login input[name="profile.lastName"]',
                '#register-site-login input[name="lastName"]',
                '#register-site-login input[placeholder*="Last"]',
            ], last)
            human_pause(0.5, 1.0)

            fill_field(page, "Password", [
                '#register-site-login input[type="password"]',
                '#register-site-login input[name="password"]',
            ], password)
            human_pause(0.5, 1.0)

            if zip_code:
                fill_field(page, "Zip code", [
                    '#register-site-login input[name="profile.zip"]',
                    '#register-site-login input[id^="gigya-textbox-"][placeholder=""]',
                ], zip_code)
                human_pause(0.5, 1.0)

            human_pause(1.0, 2.0)
            country_clean = country.strip()
            if country_clean.upper() == "DE":
                country_clean = "Germany"

            try:
                page.select_option('#gigya-dropdown-102412737448402420', value=country, timeout=5000)
            except Exception:
                try:
                    page.select_option('#gigya-dropdown-102412737448402420', label=country_clean, timeout=5000)
                except Exception:
                    page.evaluate(f"""
                        (function(){{
                            const sel = document.querySelector('#gigya-dropdown-102412737448402420');
                            if (sel) {{
                                const cv = '{country}'.trim().toUpperCase();
                                let opt = Array.from(sel.options).find(o => o.value.toUpperCase() === cv);
                                if (!opt) opt = Array.from(sel.options).find(o => o.textContent.trim().toUpperCase() === cv);
                                if (!opt && cv === "DE") opt = Array.from(sel.options).find(o => o.textContent.trim().toUpperCase() === "GERMANY");
                                if (opt) {{ sel.value = opt.value; sel.dispatchEvent(new Event('change', {{bubbles:true}})); }}
                            }}
                        }})()
                    """)
            human_pause(1.2, 2.0)

            try:
                page.select_option('select[name="data.personalization.siteLanguage"]', label="English", timeout=5000)
            except Exception:
                pass
            human_pause(0.8, 1.6)

            print("[Form] Setting checkboxes …")
            human_pause(1.5, 2.5)
            page.evaluate("""
                (function(){
                    const age = document.getElementById('gigya-checkbox-145180641846438850');
                    if (age && !age.checked) age.click();
                    const terms = document.getElementById('gigya-checkbox-terms');
                    if (terms && !terms.checked) terms.click();
                })()
            """)
            human_pause(0.8, 1.4)

            print("[Form] Submitting …")
            human_pause(1.8, 3.0)
            if not safe_click(page, 'input[type="submit"][value="Submit and Continue"]'):
                safe_click(page, 'input[type="submit"]')
            human_pause(4.0, 7.0)

            print("[Code] Waiting for code input …")
            for _ in range(20):
                if is_present(page, '#gigya-textbox-code'):
                    break
                time.sleep(1)

            if not is_present(page, '#gigya-textbox-code'):
                print("  FAILED: Code input not found")
                return False

            print("[Code] Fetching code via IMAP …")
            code = fetch_code(email_addr)
            if not code:
                print("  FAILED: Could not retrieve code")
                return False

            print(f"[Code] Entering code: {mask(code)}")
            ok = enter_verification_code(page, code)
            if not ok:
                print("  FAILED: Could not enter code")
                return False

            time.sleep(1)

            print("[Form] Clicking Verify …")
            if not safe_click(page, '#gigya-otp-update-form > div:nth-child(3) > div.gigya-composite-control.gigya-composite-control-submit > input'):
                page.evaluate("""
                    (function(){
                        const btn = document.querySelector('#gigya-otp-update-form input[type="submit"]');
                        if (btn) btn.click();
                    })()
                """)

            time.sleep(6)

            print("[Form] Waiting for profile page …")
            if not wait_for_profile_page(page, timeout=40):
                print("[WARNING] Profile page did not load in time.")

            dismiss_cookie_banner(page)

            print("[Form] Checking option …")
            try:
                page.evaluate("""
                    (function(){
                        const label = document.querySelector('label[for="lottery-55"]');
                        if (label) {
                            const input = document.getElementById('lottery-55');
                            if (input && !input.checked) label.click();
                        }
                    })()
                """)
            except Exception:
                pass
            time.sleep(1)

            chosen_year = str(random.choice(range(1956, 2006)))
            print(f"[Form] Birth year: {chosen_year}")
            try:
                page.evaluate(f"""
                    (function(){{
                        const selects = Array.from(document.querySelectorAll('select[name^="additionalCustomerAttributes-1_"]'));
                        if (selects.length > 0) {{
                            const sel = selects[0];
                            const opt = Array.from(sel.options).find(o => o.textContent.trim() === '{chosen_year}');
                            if (opt) {{ sel.value = opt.value; sel.dispatchEvent(new Event('change', {{bubbles:true}})); }}
                        }}
                    }})()
                """)
            except Exception:
                pass
            time.sleep(2)

            print("[Form] Final submit …")
            if not safe_click(page, 'ev-pl-button[data-qa="save-data-button"] button'):
                if not safe_click(page, '#main > div > app-root > app-customer-data-page > app-sports-profile > app-sports-profile-save-section > section > div > div > div > ev-pl-button button'):
                    page.evaluate("""
                        (function(){
                            const btn = document.querySelector('ev-pl-button[data-qa="save-data-button"] button');
                            if (btn) btn.click();
                        })()
                    """)

            time.sleep(8)

            if is_present(page, 'h2[data-qa="page-headline"]'):
                print("  SUCCESS")
                return True
            else:
                print("  MAYBE (success element not detected)")
                return True

    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def main():
    if not FORM_URL:
        print("Set FORM_URL env var.")
        sys.exit(1)
    if not IMAP_USER or not IMAP_PASS:
        print("Set IMAP_USER and IMAP_PASS env vars.")
        sys.exit(1)

    offset = get_offset()
    save_offset(offset + 1)

    tasks = load_tasks()
    total = len(tasks)
    state = int(START_TASK) if START_TASK else get_state()

    print(f"\n{total} tasks | starting at {state} | run #{offset}\n")

    for idx in range(state, total):
        task = tasks[idx]
        email_addr = task.get("email", "")

        max_attempts = 1
        success = False

        for attempt in range(max_attempts):
            proxy = make_proxy()
            ok = process_task(task, proxy)

            if ok is True:
                save_state(idx + 1)
                print(f"  State saved: {idx + 1}")
                success = True
                break
            elif ok == "blocked":
                print(f"  Task {idx} blocked. Stopping.")
                notify(idx, "blocked", email_addr)
                sys.exit(1)
            else:
                print(f"  Task {idx} failed. Stopping.")
                notify(idx, "failed", email_addr)
                sys.exit(1)

        if success:
            notify(idx, "success", email_addr)

        time.sleep(random.uniform(5, 10))

    print(f"\nDone. Processed {total} tasks.")


if __name__ == "__main__":
    main()
