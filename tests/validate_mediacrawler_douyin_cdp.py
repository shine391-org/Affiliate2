import os
import sys
import time
import subprocess
import asyncio
import httpx
import json
from pathlib import Path
from playwright.async_api import async_playwright

async def run_douyin_cdp_validation():
    print("=== STARTING DOUYIN CDP VALIDATION ===")
    evidence_dir = Path("data/evidence/mediacrawler/douyin")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    
    # Check if port 9222 is listening on existing browser
    port = 9222
    existing_connected = False
    try:
        res = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1.0)
        if res.status_code == 200:
            existing_connected = True
            print(f"[CDP] Found existing browser listening on port {port}: {res.json().get('Browser')}")
    except Exception:
        print(f"[CDP] Port {port} is not listening on existing Chrome.")

    proc = None
    if not existing_connected:
        print("[CDP] Launching Chrome with dedicated profile and --remote-debugging-port=9222...")
        chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        profile_path = os.path.abspath("data/douyin_cdp_profile")
        cmd = [
            chrome_exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_path}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank"
        ]
        proc = subprocess.Popen(cmd)
        # Wait up to 10s for CDP to become ready
        for i in range(20):
            await asyncio.sleep(0.5)
            try:
                res = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1.0)
                if res.status_code == 200:
                    ws_url = res.json().get("webSocketDebuggerUrl")
                    print(f"[CDP] Browser launched and ready. WebSocket URL: {ws_url}")
                    break
            except Exception:
                pass
        else:
            print("[CDP ERROR] Could not establish connection to Chrome debugging port.")
            if proc:
                proc.kill()
            return {"status": "FAIL", "reason": "Could not connect to Chrome CDP"}

    try:
        async with async_playwright() as p:
            ws_url = httpx.get(f"http://127.0.0.1:{port}/json/version").json().get("webSocketDebuggerUrl")
            browser = await p.chromium.connect_over_cdp(ws_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()

            keywords = ["AI", "Python"]
            all_records = []
            blocker_details = {}

            for kw in keywords:
                print(f"\n[DOUYIN TEST] Executing search for keyword: '{kw}'...")
                search_url = f"https://www.douyin.com/search/{kw}?type=video"
                
                # Listen to network responses to catch API data and WAF blocks
                api_responses = []
                async def handle_response(response):
                    if "aweme" in response.url or "search" in response.url:
                        try:
                            if "json" in response.headers.get("content-type", ""):
                                body = await response.json()
                                api_responses.append({"url": response.url, "status": response.status, "data": body})
                        except Exception:
                            pass

                page.on("response", handle_response)

                try:
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(5)
                except Exception as e:
                    print(f"[NAV ERROR] Navigation error: {e}")

                screenshot_path = evidence_dir / f"cdp_search_{kw}.png"
                await page.screenshot(path=str(screenshot_path))
                print(f"[EVIDENCE] Screenshot saved: {screenshot_path}")

                # Check page state
                content = await page.content()
                
                # Check for login modal or captcha
                login_detected = "登录后免费看" in content or "扫码登录" in content or "验证码" in content or "security-verify" in page.url or "captcha" in content.lower()
                print(f"[STATE] Login / Captcha Modal detected: {login_detected}")

                # Check for video cards in DOM
                cards = await page.query_selector_all("li.search-result-card, div[data-e2e='search-card'], .search-result-item")
                print(f"[DOM] Search result cards found: {len(cards)}")

                # Inspect API responses
                print(f"[NETWORK] Intercepted {len(api_responses)} aweme/search API responses.")
                for resp in api_responses:
                    data = resp.get("data", {})
                    status_code = data.get("status_code")
                    msg = data.get("status_msg") or data.get("message")
                    aweme_list = data.get("aweme_list") or data.get("data") or []
                    print(f"  -> API: {resp['url'][:80]} | HTTP {resp['status']} | status_code: {status_code} | msg: {msg} | items: {len(aweme_list) if isinstance(aweme_list, list) else 0}")
                    
                    if status_code == 2483:
                        blocker_details["waf_status_2483"] = "Argus Anti-Scraping WAF triggered"
                    if "验证" in str(msg) or "blocked" in str(msg):
                        blocker_details["auth_block"] = msg

                    if isinstance(aweme_list, list):
                        for item in aweme_list:
                            aweme_info = item.get("aweme_info") or item
                            aweme_id = aweme_info.get("aweme_id")
                            desc = aweme_info.get("desc")
                            author = aweme_info.get("author", {}).get("nickname")
                            stats = aweme_info.get("statistics", {})
                            create_time = aweme_info.get("create_time")
                            if aweme_id:
                                all_records.append({
                                    "aweme_id": aweme_id,
                                    "desc": desc,
                                    "author": author,
                                    "create_time": create_time,
                                    "digg_count": stats.get("digg_count"),
                                    "comment_count": stats.get("comment_count"),
                                    "share_count": stats.get("share_count"),
                                    "url": f"https://www.douyin.com/video/{aweme_id}"
                                })

            # Deduplicate records
            unique_records = {r["aweme_id"]: r for r in all_records}
            print(f"\n[SUMMARY] Total unique records extracted: {len(unique_records)}")

            summary = {
                "verdict": "PASS" if len(unique_records) >= 20 else "FAIL",
                "unique_records_count": len(unique_records),
                "required_count": 20,
                "blockers": blocker_details,
                "login_detected": login_detected,
                "records": list(unique_records.values())
            }

            summary_file = evidence_dir / "cdp_validation_result.json"
            with open(summary_file, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            print(f"[SAVED] Results saved to {summary_file}")

            await browser.close()
            return summary

    finally:
        if proc:
            print("[CLEANUP] Terminating background Chrome...")
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

if __name__ == "__main__":
    res = asyncio.run(run_douyin_cdp_validation())
    print("\nFINAL RESULT:", res.get("verdict"))
