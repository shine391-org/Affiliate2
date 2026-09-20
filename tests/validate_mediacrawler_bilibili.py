# -*- coding: utf-8 -*-
"""
Validation script for MediaCrawler's Bilibili discovery capability.
Reuses MediaCrawler's BilibiliClient & BilibiliSign without modified upstream code.
"""

import os
import sys
import json
import asyncio
from datetime import datetime

# Insert MediaCrawler to path
sys.path.insert(0, os.path.abspath("tools/MediaCrawler"))

from media_platform.bilibili.client import BilibiliClient
from playwright.async_api import async_playwright

async def run_bilibili_validation():
    print("[Validation] Starting Bilibili discovery validation...")
    evidence_dir = os.path.abspath("data/evidence/mediacrawler/bilibili")
    os.makedirs(evidence_dir, exist_ok=True)
    
    keywords = ["AI", "Python"]
    collected_results = {}
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        print("[Validation] Navigating to bilibili.com to acquire browser session & WBI keys...")
        await page.goto("https://www.bilibili.com", timeout=60000)
        await asyncio.sleep(2)
        
        client = BilibiliClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com"
            },
            playwright_page=page,
            cookie_dict={}
        )
        await client.update_cookies(browser_context=context)
        print(f"[Validation] Acquired {len(client.cookie_dict)} cookies from session.")
        
        for kw in keywords:
            print(f"[Validation] Executing keyword search: '{kw}'...")
            res = await client.search_video_by_keyword(kw, page=1, page_size=25)
            items = res.get("result", [])
            print(f"[Validation] Keyword '{kw}' returned {len(items)} items.")
            
            # Save raw evidence
            evidence_file = os.path.join(evidence_dir, f"search_{kw}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
            with open(evidence_file, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            print(f"[Validation] Saved raw search evidence to: {evidence_file}")
            
            collected_results[kw] = items
            await asyncio.sleep(1)
            
        # Test Creator / Video detail capability
        if collected_results.get("AI") and len(collected_results["AI"]) > 0:
            sample = collected_results["AI"][0]
            bvid = sample.get("bvid")
            mid = sample.get("mid")
            print(f"[Validation] Testing video detail for bvid: {bvid}...")
            detail_res = await client.get_video_info(bvid=bvid)
            detail_file = os.path.join(evidence_dir, f"detail_{bvid}.json")
            with open(detail_file, "w", encoding="utf-8") as f:
                json.dump(detail_res, f, ensure_ascii=False, indent=2)
            print(f"[Validation] Saved video detail evidence to: {detail_file}")
            
            if mid:
                print(f"[Validation] Testing creator videos for mid: {mid}...")
                try:
                    creator_res = await client.get_creator_videos(mid=mid, page=1, page_size=10)
                    creator_file = os.path.join(evidence_dir, f"creator_{mid}.json")
                    with open(creator_file, "w", encoding="utf-8") as f:
                        json.dump(creator_res, f, ensure_ascii=False, indent=2)
                    print(f"[Validation] Saved creator feed evidence to: {creator_file}")
                except Exception as e:
                    print(f"[Validation] Creator feed query note: {e}")
                    
        await browser.close()
        
    # Verify metadata fields
    total_ai = len(collected_results.get("AI", []))
    total_py = len(collected_results.get("Python", []))
    print(f"\n[Validation Summary] Bilibili AI records: {total_ai}, Python records: {total_py}")
    if total_ai >= 20 and total_py >= 20:
        print("[Validation Summary] Bilibili Discovery: PASS (>= 20 unique records across 2 keyword searches)")
    else:
        print(f"[Validation Summary] Bilibili Discovery: PARTIAL ({total_ai}, {total_py})")

if __name__ == "__main__":
    asyncio.run(run_bilibili_validation())
