import os
import sys
import time
import subprocess
import asyncio
import httpx
import json
from playwright.async_api import async_playwright

async def main():
    profile_dir = os.path.abspath("data/douyin_cdp_profile")
    chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    
    port = 9222
    # Ensure port is free
    cmd = [
        chrome_exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    
    print(f"Launching Chrome with user-data-dir={profile_dir} on port {port}...")
    proc = subprocess.Popen(cmd)
    
    try:
        # Wait for CDP to respond
        ws_url = None
        for _ in range(20):
            await asyncio.sleep(0.5)
            try:
                res = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1.0)
                if res.status_code == 200:
                    ws_url = res.json().get("webSocketDebuggerUrl")
                    print(f"Connected to Chrome CDP! WebSocket URL: {ws_url}")
                    break
            except Exception:
                pass
                
        if not ws_url:
            print("ERROR: Could not connect to Chrome CDP within timeout.")
            return

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(ws_url)
            print(f"Browser contexts: {len(browser.contexts)}")
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            
            # Check existing cookies
            cookies = await context.cookies("https://www.douyin.com")
            print(f"Found {len(cookies)} cookies for douyin.com:")
            for c in cookies:
                print(f"  - {c['name']} (domain: {c['domain']}, expires: {c.get('expires')})")
                
            page = await context.new_page()
            print("Navigating to https://www.douyin.com ...")
            try:
                await page.goto("https://www.douyin.com", wait_until="networkidle", timeout=30000)
            except Exception as e:
                print(f"Navigation timeout/warning: {e}")
                
            title = await page.title()
            print(f"Page title: {title}")
            
            # Check if login modal appears or if user avatar / profile is present
            content = await page.content()
            is_login_modal = "登录后免费看" in content or "扫码登录" in content or "密码登录" in content
            print(f"Login modal detected: {is_login_modal}")
            
            # Save screenshot
            os.makedirs("data/evidence/mediacrawler/douyin", exist_ok=True)
            screenshot_path = "data/evidence/mediacrawler/douyin/douyin_home.png"
            await page.screenshot(path=screenshot_path)
            print(f"Saved screenshot to {screenshot_path}")
            
            await browser.close()

    finally:
        print("Terminating Chrome process...")
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

if __name__ == "__main__":
    asyncio.run(main())
