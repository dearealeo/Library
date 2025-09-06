from __future__ import annotations  # noqa: CPY001, D100, INP001

import asyncio
import contextlib
import datetime
import functools
import pathlib
import re
import sys
import typing
import weakref

import aiofiles
import httpx
import orjson
from bs4 import BeautifulSoup
from markdownify import markdownify as md

if typing.TYPE_CHECKING:
    from collections.abc import AsyncGenerator

BASE_DIR: typing.Final = pathlib.Path(__file__).resolve().parent
README_PATH: typing.Final = BASE_DIR / "README.md"
CATALOGUE_PATH: typing.Final = BASE_DIR / "catalogue.json"

DEFAULT_HEADERS: typing.Final = {
    "accept": "text/html,*/*;q=0.01",
    "accept-language": "zh-CN,zh;q=0.9",
    "cache-control": "no-cache",
    "sec-ch-ua": '"Edge";v="107","Chromium";v="107"',
    "sec-ch-ua-mobile": "?0",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "x-requested-with": "XMLHttpRequest",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}

CST: typing.Final = datetime.timezone(datetime.timedelta(hours=8))
CCTV_PATTERN: typing.Final = re.compile(
    rb"<strong>\xe5\xa4\xae\xe8\xa7\x86\xe7\xbd\x91\xe6\xb6\x88\xe6\x81\xaf</strong>\xef\xbc\x88\xe6\x96\xb0\xe9\x97\xbb\xe8\x81\x94\xe6\x92\xad\xef\xbc\x89\xef\xbc\x9a",
)
NEWS_SELECTOR: typing.Final = ".video18847 .playingVideo .tit,.tit"
CONTENT_SELECTOR: typing.Final = "#content_area"


class Catalogue(typing.TypedDict):  # noqa: D101
    date: str


class News(typing.TypedDict):  # noqa: D101
    title: str
    content: bytes
    url: str


@functools.lru_cache(maxsize=1)
def get_current_date_formatted() -> str:  # noqa: D103
    return datetime.datetime.now(CST).strftime("%Y%m%d")


@functools.lru_cache(maxsize=1)
def get_formatted_datetime() -> str:  # noqa: D103
    return datetime.datetime.now(CST).strftime("%Y-%m-%d %H:%M")


_cache = weakref.WeakValueDictionary()


@contextlib.asynccontextmanager
async def http_client(  # noqa: D103
    timeout_s: float = 5.0,
    headers: dict[str, str] | None = None,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    cache_key = id(headers) if headers else 0
    if cache_key in _cache:
        yield _cache[cache_key]
        return

    client = httpx.AsyncClient(
        headers=headers,
        timeout=timeout_s,
        follow_redirects=True,
        limits=httpx.Limits(max_keepalive_connections=50, max_connections=200),
        http2=False,
        trust_env=False,
    )
    _cache[cache_key] = client
    try:
        yield client
    finally:
        await client.aclose()


async def fetch_url_with_retry(  # noqa: D103
    url: str,
    retries: int = 2,
    retry_delay_s: float = 0.5,
    timeout_s: float = 5.0,
    headers: dict[str, str] | None = None,
    **kwargs,  # noqa: ANN003
) -> bytes:
    merged_headers = {**DEFAULT_HEADERS, **(headers or {}), "Referer": url}

    async with http_client(timeout_s, merged_headers) as client:
        for attempt in range(retries):
            try:
                response = await client.get(url, **kwargs)
                response.raise_for_status()
                return response.content  # noqa: TRY300
            except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError):  # noqa: PERF203
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(retry_delay_s * (1.5**attempt))
            except Exception as e:
                if attempt == retries - 1:
                    msg = f"Fetch failed: {e}"
                    raise Exception(msg) from e  # noqa: TRY002
                await asyncio.sleep(retry_delay_s * (1.5**attempt))

    msg = f"Failed to fetch {url}"
    raise Exception(msg)  # noqa: TRY002


async def fetch_news_links(date_str: str) -> list[str]:  # noqa: D103
    url = f"http://tv.cctv.com/lm/xwlb/day/{date_str}.shtml"
    try:
        content = await fetch_url_with_retry(url)
        soup = BeautifulSoup(content, "lxml")
        links = {a["href"] for a in soup.find_all("a", href=True) if "shtml" in a["href"]}
        return list(links)
    except Exception:  # noqa: BLE001
        return []


async def fetch_news_item(url: str) -> News:  # noqa: D103
    try:
        content = await fetch_url_with_retry(url)
        soup = BeautifulSoup(content, "lxml")

        title_elem = soup.select_one(NEWS_SELECTOR)
        title = title_elem.text.strip().replace("[视频]", "").replace("[Video]", "") if title_elem else ""

        content_elem = soup.select_one(CONTENT_SELECTOR)
        content_bytes = str(content_elem).encode() if content_elem else b""

        return {"title": title, "content": content_bytes, "url": url}  # noqa: TRY300
    except Exception:  # noqa: BLE001
        return {"title": "", "content": b"", "url": url}


async def fetch_news_items(links: list[str]) -> list[News]:  # noqa: D103
    batch_size = min(len(links), 50)
    semaphore = asyncio.Semaphore(batch_size)

    async def fetch_with_semaphore(url: str) -> News:
        async with semaphore:
            return await fetch_news_item(url)

    return await asyncio.gather(*[fetch_with_semaphore(link) for link in links], return_exceptions=False)


def convert_news_to_markdown(news_items: list[News]) -> str:  # noqa: D103
    formatted_datetime = get_formatted_datetime()

    valid_items = [
        item for item in news_items if (title := item.get("title", "").strip()) and title and "新闻联播" not in title
    ]

    parts = [f"- 时间：{formatted_datetime}\n"]  # noqa: RUF001

    for item in valid_items:
        content_bytes = item.get("content", b"")
        if isinstance(content_bytes, bytes) and content_bytes:
            cleaned_content = CCTV_PATTERN.sub(b"", content_bytes).decode(errors="ignore")
            markdown_content = md(cleaned_content, heading_style="ATX_CLOSED").strip()
        else:
            markdown_content = ""

        parts.extend([
            f"\n## {item['title'].strip()}\n",
            f"{markdown_content}\n\n" if markdown_content else "",
            f"- [链接]({item['url']})\n",
        ])

    return "".join(parts)


async def update_catalogue_and_readme(date_str: str, news_file_path: pathlib.Path) -> None:  # noqa: D103
    try:
        catalogue_entries = []
        if CATALOGUE_PATH.exists():
            async with aiofiles.open(CATALOGUE_PATH, "rb") as f:
                catalogue_entries = orjson.loads(await f.read())

        if not any(entry.get("date") == date_str for entry in catalogue_entries):
            catalogue_entries.insert(0, {"date": date_str})
            temp_catalogue = CATALOGUE_PATH.with_suffix(".tmp")
            async with aiofiles.open(temp_catalogue, "wb") as f:
                await f.write(orjson.dumps(catalogue_entries))
            temp_catalogue.replace(CATALOGUE_PATH)

        if README_PATH.exists():
            async with aiofiles.open(README_PATH, encoding="utf-8") as f:
                readme_content = await f.read()

            readme_entry = f"- [{date_str}](./{news_file_path.relative_to(BASE_DIR).as_posix()})"
            insert_marker = "<!-- INSERT -->"

            if readme_entry not in readme_content:
                updated_readme = readme_content.replace(insert_marker, f"{insert_marker}\n{readme_entry}")
                temp_readme = README_PATH.with_suffix(".tmp")
                async with aiofiles.open(temp_readme, "w", encoding="utf-8") as f:
                    await f.write(updated_readme)
                temp_readme.replace(README_PATH)

    except Exception as e:
        msg = f"Update failed: {e}"
        raise Exception(msg) from e  # noqa: TRY002


async def main() -> None:  # noqa: D103
    current_date = get_current_date_formatted()
    year = current_date[:4]
    year_dir = BASE_DIR / year
    news_file_path = year_dir / f"{current_date}.md"

    try:
        year_dir.mkdir(parents=True, exist_ok=True)

        news_links = await fetch_news_links(current_date)
        if not news_links:
            return

        news_items = await fetch_news_items(news_links)
        if not news_items:
            return

        markdown_content = convert_news_to_markdown(news_items)

        temp_news_file = news_file_path.with_suffix(".tmp")
        async with aiofiles.open(temp_news_file, "w", encoding="utf-8") as f:
            await f.write(markdown_content)
        temp_news_file.replace(news_file_path)

        await update_catalogue_and_readme(current_date, news_file_path)

    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}", file=sys.stderr)  # noqa: T201
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
