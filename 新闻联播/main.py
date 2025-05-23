from __future__ import annotations  # noqa: CPY001, D100, INP001

import asyncio
import contextlib
import datetime
import functools
import logging
import pathlib
import re
import sys
import typing

import aiofiles
import httpx
import orjson
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md

if typing.TYPE_CHECKING:
    from collections.abc import AsyncGenerator

"""
Constants
"""


BASE_DIR: typing.Final[pathlib.Path] = pathlib.Path(__file__).resolve().parent
README_PATH: typing.Final[pathlib.Path] = BASE_DIR / "README.md"
CATALOGUE_PATH: typing.Final[pathlib.Path] = BASE_DIR / "catalogue.json"

DEFAULT_HEADERS: typing.Final[dict[str, str]] = {
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

CST: typing.Final[datetime.timezone] = datetime.timezone(datetime.timedelta(hours=8))


"""
Logging
"""


formatter: logging.Formatter = logging.Formatter(
    "%(asctime)s | %(process)d - %(processName)s | %(thread)d - %(threadName)s | %(taskName)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(pathname)s | %(message)s",  # noqa: E501
    "%Y-%m-%d %H:%M:%S,%f %z",
)
handler: logging.StreamHandler = logging.StreamHandler()
handler.setFormatter(formatter)
logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)
logger.propagate = False


"""
Types
"""


class Catalogue(typing.TypedDict):  # noqa: D101
    date: str


class News(typing.TypedDict):  # noqa: D101
    title: str
    content: str
    url: str


"""
Utility Functions
"""


@functools.lru_cache(maxsize=32)
def get_current_date_formatted() -> str:  # noqa: D103
    return datetime.datetime.now(CST).strftime("%Y%m%d")


def get_formatted_datetime() -> str:  # noqa: D103
    return datetime.datetime.now(CST).strftime("%Y-%m-%d %H:%M")


"""
Fetch
"""


@contextlib.asynccontextmanager
async def http_client(  # noqa: D103
    timeout_s: float = 10.0,
    headers: dict[str, str] | None = None,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout_s,
        follow_redirects=True,
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    ) as client:
        yield client


async def fetch_url_with_retry(  # noqa: D103
    url: str,
    retries: int = 3,
    retry_delay_s: float = 1.0,
    timeout_s: float = 10.0,
    headers: dict[str, str] | None = None,
    **kwargs: dict[str, str | int | float | bool | None],
) -> str:
    merged_headers: dict[str, str] = {
        **DEFAULT_HEADERS,
        **(headers or {}),
        "Referer": url,
    }
    last_exception: Exception | None = None

    async with http_client(timeout_s, merged_headers) as client:
        for attempt in range(retries):
            logger.debug("Fetching URL (attempt %s/%s): %s", attempt + 1, retries, url)

            try:
                response: httpx.Response = await client.get(url, **kwargs)
                response.raise_for_status()
            except (
                httpx.HTTPStatusError,
                httpx.TimeoutException,
                httpx.RequestError,
            ) as e:
                last_exception = e
                logger.warning(
                    "Failed to request HTTP (attempt %s/%s): %s - %s: %s",
                    attempt + 1,
                    retries,
                    url,
                    type(e).__name__,
                    e,
                )
                if attempt == retries - 1:
                    logger.exception("Reached maximum retries for URL: %s", url)
                    raise

                backoff_delay: float = retry_delay_s * (2**attempt)
                logger.debug("Retrying in %.2fs for URL: %s", backoff_delay, url)
                await asyncio.sleep(backoff_delay)
            except Exception as e:
                logger.exception(
                    "Unexpected error fetching URL: %s - %s",
                    url,
                    type(e).__name__,
                )
                last_exception = e
                if attempt == retries - 1:
                    msg = f"Failed after {retries} attempts: {e}"
                    raise Exception(msg) from e  # noqa: TRY002

                backoff_delay: float = retry_delay_s * (2**attempt)
                logger.debug("Retrying in %.2fs for URL: %s", backoff_delay, url)
                await asyncio.sleep(backoff_delay)
            else:
                logger.debug("Fetched URL: %s", url)
                return response.text

    raise last_exception or Exception(f"Failed to fetch {url} with {retries} retries.")


"""
Core
"""


async def fetch_news_links(date_str: str) -> list[str]:  # noqa: D103
    url: str = f"http://tv.cctv.com/lm/xwlb/day/{date_str}.shtml"
    logger.info("Fetching news index for date: %s", date_str)
    try:
        html_content: str = await fetch_url_with_retry(url)
        soup: BeautifulSoup = BeautifulSoup(
            f"<body>{html_content}</body>",
            "html.parser",
        )
        links: set[str] = {a["href"] for a in soup.find_all("a", href=True)}
        logger.info("Retrieved %s unique news links for date: %s", len(links), date_str)
        return list(links)
    except Exception as e:
        logger.exception(
            "Failed to retrieve news links for date %s: %s",
            date_str,
            type(e).__name__,
        )
        return []


async def fetch_news_item(url: str) -> News:  # noqa: D103
    logger.debug("Fetching news item: %s", url)
    try:
        html_content: str = await fetch_url_with_retry(url)
        soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")

        title_element: Tag | None = soup.select_one(".video18847 .playingVideo .tit")
        if not title_element or not title_element.text.strip():
            title_element = soup.select_one(".tit")

        title: str = title_element.text.strip().replace("[视频]", "") if title_element else ""

        content_element: Tag | None = soup.select_one("#content_area")
        content: str = str(content_element) if content_element else ""

        if not title:
            logger.warning("Missing title in news item: %s", url)
        if not content:
            logger.warning("Missing content in news item: %s", url)

    except Exception as e:
        logger.exception("Failed to fetch news item: %s - %s", url, type(e).__name__)
        return {"title": "Failed to fetch", "content": "", "url": url}
    else:
        logger.debug("Fetched news item: %s", url)
        return {"title": title, "content": content, "url": url}


async def fetch_news_items(links: list[str]) -> list[News]:  # noqa: D103
    logger.info("Starting batch fetch of %s news items", len(links))
    batch_size: int = min(max(5, len(links) // 10), 20)

    semaphore: asyncio.Semaphore = asyncio.Semaphore(batch_size)
    logger.debug("Using concurrency limit of %s for news item fetching", batch_size)

    async def fetch_with_semaphore(url: str) -> News:
        async with semaphore:
            return await fetch_news_item(url)

    results: list[News] = await asyncio.gather(
        *[fetch_with_semaphore(link) for link in links],
    )

    logger.info(
        "Completed batch fetch: %s/%s news items retrieved",
        len(results),
        len(links),
    )
    return results


CCTV_PATTERN: re.Pattern = re.compile(r"<strong>央视网消息</strong>（新闻联播）：")  # noqa: RUF001
# INDENT_PATTERN: re.Pattern = re.compile(r"^(\s{2})-", re.MULTILINE)
# EMPTY_P_PATTERN: re.Pattern = re.compile(r"<p><br></p><p><strong>")
# EMPTY_P_BR_PATTERN: re.Pattern = re.compile(r"<p><br/></p><p><strong>")


# def clean_news_content(content: str) -> str:
#     if not content:
#         return ""
#     content = CCTV_PATTERN.sub("", content)
#     content = INDENT_PATTERN.sub(r"    -", content)
#     content = EMPTY_P_PATTERN.sub("<p></p><p><strong>", content)
#     content = EMPTY_P_BR_PATTERN.sub("<p></p><p><strong>", content)
#     return content.strip()


def convert_news_to_markdown(news_items: list[News]) -> str:  # noqa: D103
    formatted_datetime: str = get_formatted_datetime()

    valid_items: list[News] = [
        item
        for item in news_items
        if (title := item.get("title", ""))
        and title.strip()
        and "《新闻联播》" not in title
        and title != "Failed to fetch"
    ]

    logger.debug(
        "Converting %s/%s valid news items to markdown",
        len(valid_items),
        len(news_items),
    )

    markdown_parts: list[str] = [f"- 时间：{formatted_datetime}\n"]  # noqa: RUF001
    markdown_parts.extend(
        f"\n## {item['title'].strip()}\n"
        f"{md(CCTV_PATTERN.sub('', content), heading_style='ATX_CLOSED').strip() if (content := item.get('content', '')) and isinstance(content, str) and content.strip() else ''}\n\n"  # noqa: E501
        f"- [链接]({item['url']})\n"
        for item in valid_items
    )

    return "".join(markdown_parts)


async def update_catalogue_and_readme(date_str: str, news_file_path: pathlib.Path) -> None:  # noqa: D103
    logger.info("Updating catalogue and README for date: %s", date_str)
    try:
        async with contextlib.AsyncExitStack():
            catalogue_entries: list[Catalogue] = []
            if CATALOGUE_PATH.exists():
                async with aiofiles.open(CATALOGUE_PATH, "rb") as f:
                    catalogue_content = await f.read()
                    catalogue_entries = orjson.loads(catalogue_content)

            if not any(entry.get("date") == date_str for entry in catalogue_entries):
                catalogue_entries.insert(0, {"date": date_str})
                temp_catalogue: pathlib.Path = CATALOGUE_PATH.with_suffix(".tmp")
                async with aiofiles.open(temp_catalogue, "wb") as f:
                    await f.write(orjson.dumps(catalogue_entries, option=orjson.OPT_INDENT_2))
                temp_catalogue.replace(CATALOGUE_PATH)
                logger.info("Added new date entry to catalogue: %s", date_str)
            else:
                logger.debug("Date already exists in catalogue: %s", date_str)

            if README_PATH.exists():
                async with aiofiles.open(README_PATH, encoding="utf-8") as f:
                    readme_content: str = await f.read()

                readme_entry: str = f"- [{date_str}](./{news_file_path.relative_to(BASE_DIR).as_posix()})"
                insert_marker: str = "<!-- INSERT -->"

                if readme_entry not in readme_content:
                    updated_readme: str = readme_content.replace(
                        insert_marker,
                        f"{insert_marker}\n{readme_entry}",
                    )
                    temp_readme: pathlib.Path = README_PATH.with_suffix(".tmp")
                    async with aiofiles.open(temp_readme, "w", encoding="utf-8") as f:
                        await f.write(updated_readme)
                    temp_readme.replace(README_PATH)
                    logger.info("Added new entry to README: %s", date_str)
                else:
                    logger.debug("Entry already exists in README: %s", date_str)
            else:
                logger.warning(
                    "README file not found at expected location: %s",
                    README_PATH,
                )

    except Exception as e:
        logger.exception("Failed to update catalogue or README: %s", type(e).__name__)
        raise


"""
Main
"""


async def main() -> None:  # noqa: D103
    current_date: str = get_current_date_formatted()
    year: str = current_date[:4]
    year_dir: pathlib.Path = BASE_DIR / year
    news_file_path: pathlib.Path = year_dir / f"{current_date}.md"

    logger.info("Starting news collection process for date: %s", current_date)

    try:
        year_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("Ensured directory exists: %s", year_dir)

        news_links: list[str] = await fetch_news_links(current_date)
        if not news_links:
            logger.warning("No news links found for date: %s", current_date)
            return

        news_items: list[News] = await fetch_news_items(news_links)
        if not news_items:
            logger.warning(
                "No news content could be retrieved for date: %s",
                current_date,
            )
            return

        markdown_content: str = convert_news_to_markdown(news_items)

        temp_news_file: pathlib.Path = news_file_path.with_suffix(".tmp")
        with temp_news_file.open("w", encoding="utf-8") as f:
            f.write(markdown_content)
        temp_news_file.replace(news_file_path)
        logger.info("Saved news content to file: %s", news_file_path)

        await update_catalogue_and_readme(current_date, news_file_path)

        logger.info(
            "News collection process completed successfully for date: %s",
            current_date,
        )

    except Exception as e:
        logger.exception("News collection process failed: %s", type(e).__name__)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
