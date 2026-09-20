import os
import asyncio
import httpx
from playwright.async_api import async_playwright

async def get_douyin_video_stream(url, out_name):
    print(f"\n--- Checking Douyin Video: {url} ---")
    video_urls = []
    
    async with async_playwright() as p:
        # Launch chromium with anti-detection args
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-web-security",
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )
        page = await context.new_page()

        async def on_response(response):
            # Check for video streams
            ct = response.headers.get("content-type", "")
            u = response.url
            if "video" in ct or ".mp4" in u or "douyinvod.com" in u:
                if "mime_type=video_mp4" in u or ".mp4" in u or "video" in ct:
                    print(f"Captured Video Stream URL: {u[:100]}... (Content-Type: {ct})")
                    video_urls.append(u)

        page.on("response", on_response)

        try:
            print(f"Navigating to {url} ...")
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(6)
        except Exception as e:
            print(f"Navigation warning: {e}")

        # Check DOM for <video> tag src
        video_src = await page.evaluate("() => { const v = document.querySelector('video'); return v ? v.src : null; }")
        print(f"DOM video.src: {video_src[:100] if video_src else None}")
        if video_src:
            video_urls.append(video_src)

        await page.screenshot(path=f"data/evidence/mediacrawler/douyin/{out_name}_page.png")
        await browser.close()

    # Filter out static placeholder uuu_265.mp4
    real_streams = [u for u in video_urls if "uuu_265" not in u and ("zjcdn.com" in u or "douyinvod.com" in u or "byteoversea" in u or "aweme" in u)]
    if not real_streams and video_urls:
        real_streams = video_urls

    if real_streams:
        stream_url = real_streams[0]
        print(f"Downloading REAL video from: {stream_url[:120]}...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Referer": "https://www.douyin.com/",
            "Range": "bytes=0-10000000",
        }
        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            res = await client.get(stream_url, timeout=30.0)
            if res.status_code in (200, 206) and len(res.content) > 10000:
                out_path = f"data/samples/{out_name}.mp4"
                with open(out_path, "wb") as f:
                    f.write(res.content)
                print(f"SUCCESS: Saved {len(res.content)} bytes to {out_path}")
                return out_path
            else:
                print(f"Download response status: {res.status_code}, length: {len(res.content)}")

    print(f"FAIL: No video stream could be captured for {url}")
    return None

async def main():
    os.makedirs("data/samples", exist_ok=True)
    os.makedirs("data/evidence/mediacrawler/douyin", exist_ok=True)
    
    u1 = "https://www.douyin.com/video/7525538910311632128"
    u2 = "https://www.douyin.com/video/7525082444551310602"
    
    r1 = await get_douyin_video_stream(u1, "real_douyin_sample1")
    r2 = await get_douyin_video_stream(u2, "real_douyin_sample2")
    
    print("\nSummary:", {"sample1": r1, "sample2": r2})

if __name__ == "__main__":
    asyncio.run(main())
