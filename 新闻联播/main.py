import asyncio
import logging
import re
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Final, List, Optional, Set, TypedDict

import httpx
import orjson
from bs4 import BeautifulSoup, Tag

"""
Constants
"""
BASE_DIR: Final[Path] = Path(__file__).resolve().parent
LOG_PATH: Final[Path] = BASE_DIR / "main.log"
NEWS_DIR: Final[Path] = BASE_DIR / "新闻联播"
README_PATH: Final[Path] = BASE_DIR / "README.md"
CATALOGUE_PATH: Final[Path] = BASE_DIR / "catalogue.json"

DEFAULT_HEADERS: Final[Dict[str, str]] = {
    "accept": "text/html, */*; q=0.01",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "sec-ch-ua": '"Microsoft Edge";v="107", "Chromium";v="107", "Not=A?Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "x-requested-with": "XMLHttpRequest",
    "cookie": "cna=eY6BGb2h7yACAbSMsOm2vFG2; sca=5a4237a6; atpsida=6e052f524a88bc925aed09c0_1664038526_68",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}

CST: Final[timezone] = timezone(timedelta(hours=8))

"""
Logging
"""

formatter: logging.Formatter = logging.Formatter(
    "%(asctime)s | %(process)d - %(processName)s | %(thread)d - %(threadName)s | %(taskName)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(pathname)s | %(message)s",
    "%Y-%m-%d %H:%M:%S,%f %z",
)
handler: RotatingFileHandler = RotatingFileHandler(
    LOG_PATH, maxBytes=1024 * 1024, backupCount=1, encoding="utf-8", delay=True
)
handler.setFormatter(formatter)
logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)
logger.propagate = False


"""
Types
"""


class Catalogue(TypedDict):
    date: str


class News(TypedDict):
    title: str
    content: str
    url: str


"""
Utility Functions
"""


@lru_cache(maxsize=32)
def get_current_date_formatted() -> str:
    return datetime.now(CST).strftime("%Y%m%d")


def get_formatted_datetime() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M")


"""
Fetch
"""


@asynccontextmanager
async def http_client(
    timeout_s: float = 10.0, headers: Optional[Dict[str, str]] = None
) -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout_s,
        follow_redirects=True,
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    ) as client:
        yield client


async def fetch_url_with_retry(
    url: str,
    retries: int = 3,
    retry_delay_s: float = 1.0,
    timeout_s: float = 10.0,
    headers: Optional[Dict[str, str]] = None,
    **kwargs: Any,
) -> str:
    merged_headers: Dict[str, str] = {
        **DEFAULT_HEADERS,
        **(headers or {}),
        "Referer": url,
    }
    last_exception: Optional[Exception] = None

    async with http_client(timeout_s, merged_headers) as client:
        for attempt in range(retries):
            try:
                logger.debug(f"Fetching URL (attempt {attempt+1}/{retries}): {url}")
                response: httpx.Response = await client.get(url, **kwargs)
                response.raise_for_status()
                logger.debug(f"Fetched URL: {url}")
                return response.text
            except (
                httpx.HTTPStatusError,
                httpx.TimeoutException,
                httpx.RequestError,
            ) as e:
                last_exception = e
                logger.warning(
                    f"Failed to request HTTP (attempt {attempt+1}/{retries}): {url} - {type(e).__name__}: {e}"
                )
                if attempt == retries - 1:
                    logger.error(f"Reached maximum retries for URL: {url}")
                    raise

                backoff_delay: float = retry_delay_s * (2**attempt)
                logger.debug(f"Retrying in {backoff_delay:.2f}s for URL: {url}")
                await asyncio.sleep(backoff_delay)
            except Exception as e:
                logger.exception(
                    f"Unexpected error fetching URL: {url} - {type(e).__name__}: {e}"
                )
                last_exception = e
                if attempt == retries - 1:
                    raise Exception(f"Failed after {retries} attempts: {e}") from e
                backoff_delay: float = retry_delay_s * (2**attempt)
                logger.debug(f"Retrying in {backoff_delay:.2f}s for URL: {url}")
                await asyncio.sleep(backoff_delay)

    raise last_exception or Exception(f"Failed to fetch {url} with {retries} retries.")


"""
Core
"""


async def fetch_news_links(date_str: str) -> List[str]:
    url: str = f"http://tv.cctv.com/lm/xwlb/day/{date_str}.shtml"
    logger.info(f"Fetching news index for date: {date_str}")
    try:
        html_content: str = await fetch_url_with_retry(url)
        soup: BeautifulSoup = BeautifulSoup(
            f"<body>{html_content}</body>", "html.parser", features="lxml"
        )
        links: Set[str] = {a["href"] for a in soup.find_all("a", href=True)}
        logger.info(f"Retrieved {len(links)} unique news links for date: {date_str}")
        return list(links)
    except Exception as e:
        logger.exception(
            f"Failed to retrieve news links for date {date_str}: {type(e).__name__}"
        )
        return []


async def fetch_news_item(url: str) -> News:
    logger.debug(f"Fetching news item: {url}")
    try:
        html_content: str = await fetch_url_with_retry(url)
        soup: BeautifulSoup = BeautifulSoup(
            html_content, "html.parser", features="lxml"
        )

        title_element: Optional[Tag] = soup.select_one(".cnt_nav h3")
        title: str = (
            title_element.text.strip().replace("[视频]", "") if title_element else ""
        )

        content_element: Optional[Tag] = soup.select_one("#content_area")
        content: str = str(content_element) if content_element else ""

        if not title:
            logger.warning(f"Missing title in news item: {url}")
        if not content:
            logger.warning(f"Missing content in news item: {url}")

        return {"title": title, "content": content, "url": url}
    except Exception as e:
        logger.exception(f"Failed to fetch news item: {url} - {type(e).__name__}")
        return {"title": "Failed to fetch", "content": "", "url": url}


async def fetch_news_items(links: List[str]) -> List[News]:
    logger.info(f"Starting batch fetch of {len(links)} news items")
    batch_size: int = min(max(5, len(links) // 10), 20)

    semaphore: asyncio.Semaphore = asyncio.Semaphore(batch_size)
    logger.debug(f"Using concurrency limit of {batch_size} for news item fetching")

    async def fetch_with_semaphore(url: str) -> News:
        async with semaphore:
            return await fetch_news_item(url)

    results: List[News] = await asyncio.gather(
        *[fetch_with_semaphore(link) for link in links]
    )

    logger.info(
        f"Completed batch fetch: {len(results)}/{len(links)} news items retrieved"
    )
    return results


CCTV_PATTERN: re.Pattern = re.compile(r"<strong>央视网消息</strong>（新闻联播）：")
INDENT_PATTERN: re.Pattern = re.compile(r"^(\s{2})-", re.MULTILINE)
EMPTY_P_PATTERN: re.Pattern = re.compile(r"<p><br></p><p><strong>")


def clean_news_content(content: str) -> str:
    if not content:
        return ""
    content = CCTV_PATTERN.sub("", content)
    content = INDENT_PATTERN.sub(r"    -", content)
    content = EMPTY_P_PATTERN.sub("<p></p><p><strong>", content)
    return content.strip()


def convert_news_to_markdown(news_items: List[News]) -> str:
    formatted_datetime: str = get_formatted_datetime()

    valid_items: List[News] = [
        item
        for item in news_items
        if item.get("title", "").strip()
        and "《新闻联播》" not in item.get("title", "")
        and item.get("title") != "Failed to fetch"
    ]

    logger.debug(
        f"Converting {len(valid_items)}/{len(news_items)} valid news items to markdown"
    )

    markdown_parts: List[str] = [f"- 时间：{formatted_datetime}\n"]
    markdown_parts.extend(
        f"\n## {item['title'].strip()}\n"
        f"{clean_news_content(item['content'])}\n"
        f"- [链接]({item['url']})\n"
        for item in valid_items
    )

    return "".join(markdown_parts)


async def update_catalogue_and_readme(date_str: str, news_file_path: Path) -> None:
    logger.info(f"Updating catalogue and README for date: {date_str}")
    try:
        async with AsyncExitStack():
            catalogue_entries: List[Catalogue] = []
            if CATALOGUE_PATH.exists():
                with open(CATALOGUE_PATH, "rb") as f:
                    catalogue_entries = orjson.loads(f.read())

            if not any(entry.get("date") == date_str for entry in catalogue_entries):
                catalogue_entries.insert(0, {"date": date_str})
                temp_catalogue: Path = CATALOGUE_PATH.with_suffix(".tmp")
                with open(temp_catalogue, "wb") as f:
                    f.write(orjson.dumps(catalogue_entries, option=orjson.OPT_INDENT_2))
                temp_catalogue.replace(CATALOGUE_PATH)
                logger.info(f"Added new date entry to catalogue: {date_str}")
            else:
                logger.debug(f"Date already exists in catalogue: {date_str}")

            if README_PATH.exists():
                with open(README_PATH, encoding="utf-8") as f:
                    readme_content: str = f.read()

                readme_entry: str = (
                    f"- [{date_str}](./{news_file_path.relative_to(BASE_DIR).as_posix()})"
                )
                insert_marker: str = "<!-- INSERT -->"

                if readme_entry not in readme_content:
                    updated_readme: str = readme_content.replace(
                        insert_marker, f"{insert_marker}\n{readme_entry}"
                    )
                    temp_readme: Path = README_PATH.with_suffix(".tmp")
                    with open(temp_readme, "w", encoding="utf-8") as f:
                        f.write(updated_readme)
                    temp_readme.replace(README_PATH)
                    logger.info(f"Added new entry to README: {date_str}")
                else:
                    logger.debug(f"Entry already exists in README: {date_str}")
            else:
                logger.warning(
                    f"README file not found at expected location: {README_PATH}"
                )

    except Exception as e:
        logger.exception(f"Failed to update catalogue or README: {type(e).__name__}")
        raise


"""
Main
"""


async def main() -> None:
    current_date: str = get_current_date_formatted()
    year: str = current_date[:4]
    year_dir: Path = NEWS_DIR / year
    news_file_path: Path = year_dir / f"{current_date}.md"

    logger.info(f"Starting news collection process for date: {current_date}")

    try:
        year_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured directory exists: {year_dir}")

        news_links: List[str] = await fetch_news_links(current_date)
        if not news_links:
            logger.warning(f"No news links found for date: {current_date}")
            return

        news_items: List[News] = await fetch_news_items(news_links)
        if not news_items:
            logger.warning(
                f"No news content could be retrieved for date: {current_date}"
            )
            return

        markdown_content: str = convert_news_to_markdown(news_items)

        temp_news_file: Path = news_file_path.with_suffix(".tmp")
        with open(temp_news_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        temp_news_file.replace(news_file_path)
        logger.info(f"Saved news content to file: {news_file_path}")

        await update_catalogue_and_readme(current_date, news_file_path)

        logger.info(
            f"News collection process completed successfully for date: {current_date}"
        )

    except Exception as e:
        logger.exception(f"News collection process failed: {type(e).__name__}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
