from __future__ import annotations  # noqa: CPY001, D100, EXE002, INP001

import abc
import argparse
import base64
import collections
import concurrent.futures
import contextlib
import functools
import logging
import os
import pathlib
import random
import re
import sqlite3
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Final, TypeAlias

import orjson
import requests
from bs4 import BeautifulSoup, Tag
from docx import Document
from docx.document import Document as _Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell, _Row  # noqa: PLC2701
from docx.text.paragraph import Paragraph
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

DBROW: TypeAlias = tuple[
    str | None,  # id
    str | None,  # title
    str | None,  # url
    str | None,  # office
    str | None,  # type
    int | None,  # status
    str | None,  # publish
    str | None,  # expiry
    int,  # saved
    int,  # parsed
    str | None,  # bbbs_id
    str | None,  # source_api
]
APIRESULT: TypeAlias = dict[str, Any]
LAWDATA: TypeAlias = dict[str, Any]
BASE_DIR: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parent
DB_PATH: Final[pathlib.Path] = BASE_DIR / "database.db"
CPU_COUNT: Final[int] = os.cpu_count() or 1
DEF_INIT_DELAY: Final[int] = 2
DEF_REQ_DELAY: Final[float] = 5.0
ANTI_BOT_DELAY: Final[float] = 30.0
MAX_THREADS_RATIO: Final[float] = 1.0
API_PAGE_SIZE: Final[int] = 10
HTTP_TIMEOUT: Final[tuple[float, float]] = (3.05, 10.0)
LAW_TYPES: Final[dict[int, tuple[str, str]]] = {
    1: ("xffl", "宪法"),
    2: ("flfg", "法律"),
    3: ("xzfg", "行政法规"),
    4: ("jcfg", "监察法规"),
    5: ("sfjs", "司法解释"),
    6: ("dfxfg", "地方性法规"),
    7: ("flfg_fl", "法律"),
    8: ("flfg_fljs", "法律解释"),
    9: ("flfg_fljswd", "有关法律问题和重大问题的决定"),
    10: ("flfg_xgfzdd", "修改、废止的决定"),
}
FLFG_CODE_MAP: Final[dict[str, int]] = {
    "宪法": 100,
    "法律_修改废止决定": 200,
    "法律_修正案": 195,
    "法律_法律问题决定": 190,
    "法律解释": 180,
    "法律_诉讼非诉讼程序法": 170,
    "法律_刑法": 160,
    "法律_社会法": 150,
    "法律_经济法": 140,
    "法律_行政法": 130,
    "法律_民法商法": 120,
    "法律_宪法相关法": 110,
    "行政法规_行政法规": 210,
    "行政法规_修改废止决定": 215,
    "监察法规": 220,
    "司法解释_高法司法解释": 320,
    "司法解释_高检司法解释": 330,
    "司法解释_联合发布司法解释": 340,
    "司法解释_修改废止决定": 350,
    "地方法规_修改废止决定": 310,
    "地方法规_法规性决定": 305,
    "地方法规_地方性法规": 230,
    "地方法规_自治条例": 260,
    "地方法规_单行条例": 270,
    "地方法规_经济特区法规": 290,
    "地方法规_浦东新区法规": 295,
    "地方法规_海南自由贸易港法规": 300,
}
LAW_CATEGORIES: Final[tuple[str, ...]] = tuple(details[0] for details in LAW_TYPES.values())
BUILD_TYPE_MAP: dict[str, int] = {
    "宪法": 1,
    "法律": 7,
    "法律解释": 8,
    "有关法律问题和重大问题的决定": 9,
    "修改、废止的决定": 10,
    "行政法规": 3,
    "监察法规": 4,
    "司法解释": 5,
    "地方性法规": 6,
    "地方法规": 6,
}
API_TYPE_TO_ID_MAP: Final[dict[str, int]] = {
    **{code: type_id for type_id, (code, _) in LAW_TYPES.items()},
    **BUILD_TYPE_MAP,
}
del BUILD_TYPE_MAP
LAW_TABLE_SCHEMA: Final[Mapping[str, str]] = {
    "id": "TEXT PRIMARY KEY NOT NULL",
    "title": "TEXT NOT NULL",
    "url": "TEXT NOT NULL",
    "office": "TEXT NOT NULL",
    "type": "TEXT NOT NULL",
    "status": "INTEGER NOT NULL",
    "publish": "TEXT NOT NULL",
    "expiry": "TEXT NOT NULL",
    "saved": "INTEGER DEFAULT 0",
    "parsed": "INTEGER DEFAULT 0",
    "bbbs_id": "TEXT DEFAULT NULL",
    "source_api": "TEXT DEFAULT 'old'",
}
METADATA_SCHEMA: Final[Mapping[str, str]] = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL",
    "key": "TEXT NOT NULL UNIQUE",
    "value": "TEXT NOT NULL",
}
SEARCH_LIST_URL: Final[str] = "https://flk.npc.gov.cn/law-search/search/list"
SINGLE_DOWNLOAD_URL: Final[str] = "https://flk.npc.gov.cn/law-search/download/pc"
LIST_PAGE_URL: Final[str] = SEARCH_LIST_URL
BASE_SITE_URL: Final[str] = "https://flk.npc.gov.cn/search"
REQUIRED_COOKIES = frozenset({"_yfx_session", "wzws_sessionid"})
BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-web-security",
    "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",  # noqa: E501
]
HEADERS_TEMPLATE = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
    "DNT": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",  # noqa: E501
    "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-gpc": "1",
}
API_PAYLOAD_BASE = {
    "sxrq": [],
    "gbrq": [],
    "sxx": [],
    "gbrqYear": [],
    "zdjgCodeId": [],
    "searchContent": "",
    "orderByParam": {"order": "-1", "sort": ""},
}
CONTENT_TYPE_PATTERNS = ("application/", "binary/")
HTML_DETECTION_PATTERNS = ("<html", "<!DOCTYPE", "<noscript")
ANTIBOT_JS_PATTERNS = ("function(", "parseInt(", "while(")
cached_cookies: dict[str, str] = {}
NUMBER_RE: Final[str] = r"[一二三四五六七八九十零百千万\d]"
INDENT_RE: Final[list[str]] = [
    r"序言",
    rf"^第{NUMBER_RE}+编",
    rf"^第{NUMBER_RE}+分编",
    rf"^第{NUMBER_RE}+章",
    rf"^第{NUMBER_RE}+节",
    r"^([一二三四五六七八九十零百千万]+、.{1,15})[^。；：]$",  # noqa: RUF001
]
LINE_RE: Final[list[str]] = [*INDENT_RE, rf"^第{NUMBER_RE}+条"]
DESC_REMOVE_PATTERNS: Final[tuple[str, ...]] = (
    r"^（",  # noqa: RUF001
    r"^\(",
    r"）$",  # noqa: RUF001
    r"\)$",
    r"^根据",
    r"^自",
)
LINE_START: Final[str] = (
    rf"""^({"|".join(f"({pattern.replace(NUMBER_RE, '一')})" for pattern in (p for p in LINE_RE if "节" not in p))})"""
)
FLFG_REVERSE_MAP: Final[dict[int, str]] = {v: k for k, v in FLFG_CODE_MAP.items()}
PATH_SANITIZER: Final[re.Pattern[str]] = re.compile(r'[/\\:*?"<>|]')


log_formatter: logging.Formatter = logging.Formatter(
    "%(asctime)s | %(process)d - %(processName)s | %(thread)d - %(threadName)s | %(taskName)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(pathname)s | %(message)s",  # noqa: E501
    "%Y-%m-%d %H:%M:%S,%f %z",
)
log_handler: logging.StreamHandler[Any] = logging.StreamHandler()
log_handler.setFormatter(log_formatter)
logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(log_handler)
logger.propagate = False


@functools.lru_cache(maxsize=len(LAW_TYPES))
def get_type_code(type_id: int) -> str:  # noqa: D103
    return LAW_TYPES.get(type_id, ("", ""))[0]


@functools.lru_cache(maxsize=len(LAW_TYPES))
def get_type_name(type_id: int) -> str:  # noqa: D103
    return LAW_TYPES.get(type_id, ("", ""))[1]


@functools.lru_cache(maxsize=512)
def extract_region_from_office(office: str) -> str | None:  # noqa: D103
    return ((m := re.search(r"^(.*?)人民代表大会", office or "")) and re.sub(r"常务委员会$", "", m[1].strip())) or None


@functools.lru_cache(maxsize=256)
def _sanitize_folder_name(name: str) -> str:
    return PATH_SANITIZER.sub("_", name.strip()) if name else ""


@functools.lru_cache(maxsize=128)
def _resolve_flfg_folder(flfg_code_id: int) -> str:
    return FLFG_REVERSE_MAP.get(flfg_code_id, "")


@functools.lru_cache(maxsize=256)
def _match_api_type_to_flfg(api_type: str) -> str:
    if not api_type:
        return ""

    api_lower = api_type.lower()
    api_type.rsplit("_", maxsplit=1)[-1] if "_" in api_type else api_type

    for flfg_name in FLFG_CODE_MAP:
        flfg_lower = flfg_name.lower()
        if (
            api_lower in flfg_lower
            or flfg_lower.endswith(api_lower)
            or api_lower.endswith(flfg_name.split("_")[-1].lower())
        ):
            return flfg_name

    return api_type


def get_path(  # noqa: D103
    api_type: str,
    main_type_id: int,
    flfg_code_id: int | None = None,
    office: str | None = None,
) -> pathlib.Path:
    base_dir = BASE_DIR / get_type_name(main_type_id)

    if flfg_code_id is not None and (folder_name := _resolve_flfg_folder(flfg_code_id)):
        type_name = get_type_name(main_type_id)
        if folder_name.endswith(f"_{type_name}") or folder_name == type_name:
            if "_" in folder_name:
                target_dir = base_dir / _sanitize_folder_name(folder_name.split("_", 1)[1])
            else:
                target_dir = base_dir
        else:
            target_dir = base_dir / _sanitize_folder_name(folder_name)

        if main_type_id == 6 and office and "地方" in folder_name and (region := extract_region_from_office(office)):  # noqa: PLR2004
            return target_dir / region

        return target_dir

    if api_type:
        matched_folder = _match_api_type_to_flfg(api_type)
        type_name = get_type_name(main_type_id)

        if matched_folder.endswith(f"_{type_name}") or matched_folder == type_name:
            if "_" in matched_folder:
                target_dir = base_dir / _sanitize_folder_name(matched_folder.split("_", 1)[1])
            else:
                target_dir = base_dir
        else:
            target_dir = base_dir / _sanitize_folder_name(matched_folder)

        if main_type_id == 6 and office and (region := extract_region_from_office(office)):  # noqa: PLR2004
            return target_dir / region

        return target_dir

    return base_dir


@functools.lru_cache(maxsize=len(LAW_TYPES))
def get_flfg_code_id(type_id: int) -> list[int]:  # noqa: C901, D103, PLR0911
    if type_id == 0:
        return []
    if type_id == 1:  # 宪法
        return [100]
    if type_id == 2:  # 法律  # noqa: PLR2004
        return [110, 120, 130, 140, 150, 160, 170, 180, 190, 195, 200]
    if type_id == 3:  # 行政法规  # noqa: PLR2004
        return [210, 215]
    if type_id == 4:  # 监察法规  # noqa: PLR2004
        return [220]
    if type_id == 5:  # 司法解释  # noqa: PLR2004
        return [320, 330, 340, 350]
    if type_id == 6:  # 地方性法规  # noqa: PLR2004
        return [230, 260, 270, 290, 295, 300, 305, 310]
    if type_id == 7:  # 法律  # noqa: PLR2004
        return [110, 120, 130, 140, 150, 160, 170]
    if type_id == 8:  # 法律解释  # noqa: PLR2004
        return [180]
    if type_id == 9:  # 有关法律问题和重大问题的决定  # noqa: PLR2004
        return [190]
    if type_id == 10:  # 修改、废止的决定  # noqa: PLR2004
        return [200]
    return []


def create_sql(table_name: str, schema: Mapping[str, str]) -> str:  # noqa: D103
    return f'CREATE TABLE IF NOT EXISTS "{table_name}" ({", ".join(f"{k} {v}" for k, v in schema.items())});'


def initialize_database() -> None:  # noqa: D103
    _exec_db_transaction(
        lambda c: (
            c.executescript(_build_schema_sql()),
            c.executemany("INSERT OR IGNORE INTO info (key, value) VALUES (?, ?)", _build_metadata()),
        ),
        "Database initialization",
    )


def update_schema() -> None:  # noqa: D103
    _exec_db_transaction(
        lambda c: _update_schema_columns(c),  # noqa: PLW0108
        "Database schema update",
    )


def _exec_db_transaction(operation, operation_name: str) -> None:  # noqa: ANN001
    logger.info("Starting %s...", operation_name.lower())
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0)) as conn:
            conn.executescript(_get_pragma())
            with conn:
                operation(conn.cursor())
        logger.info("%s complete.", operation_name)
    except sqlite3.Error as e:
        logger.critical("%s failed: %s", operation_name, e, exc_info=True)
        msg = f"{operation_name} error: {e}"
        raise SystemExit(msg) from e


def _get_pragma() -> str:
    return (
        "PRAGMA journal_mode=WAL;"
        "PRAGMA synchronous=NORMAL;"
        "PRAGMA cache_size=-10000;"
        "PRAGMA temp_store=MEMORY;"
        "PRAGMA foreign_keys=ON;"
        "PRAGMA busy_timeout=5000;"
    )


def _build_schema_sql() -> str:
    return f"{create_sql('info', METADATA_SCHEMA)};{';'.join(create_sql(cat, LAW_TABLE_SCHEMA) for cat in LAW_CATEGORIES)};"  # noqa: E501


def _build_metadata() -> list[tuple[str, str]]:
    return [
        ("init_complete", "true"),
        *[(f"law_type_{k}", f"{v[0]}:{v[1]}") for k, v in LAW_TYPES.items()],
    ]


def _update_schema_columns(cursor) -> None:  # noqa: ANN001
    new_columns = [
        ("flfgCodeId", "INTEGER DEFAULT NULL"),
        ("source_type", "TEXT DEFAULT 'old_api'"),
        ("bbbs_id", "TEXT DEFAULT NULL"),
        ("source_api", "TEXT DEFAULT 'old'"),
    ]

    for table_name in LAW_CATEGORIES:
        if not cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone():
            continue

        existing_cols = {row[1] for row in cursor.execute(f"PRAGMA table_info({table_name})")}

        for col_name, col_def in new_columns:
            if col_name not in existing_cols:
                try:
                    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")
                except sqlite3.Error as e:
                    logger.warning("Column %s addition failed for %s: %s", col_name, table_name, e)

    cursor.executemany(
        "INSERT OR REPLACE INTO info (key, value) VALUES (?, ?)",
        [
            ("flfgCodeId_mapping", str(FLFG_CODE_MAP)),
            ("schema_version", "2.0"),
            ("last_schema_update", str(int(time.time()))),
        ],
    )


def get_cookies() -> dict[str, str]:  # noqa: D103
    global cached_cookies  # noqa: PLW0603
    if cached_cookies:
        return cached_cookies.copy()
    if not PLAYWRIGHT_AVAILABLE:
        return {}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=HEADERS_TEMPLATE["User-Agent"],
            )
            page = context.new_page()
            page.goto(BASE_SITE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            cached_cookies = {
                c["name"]: c["value"]
                for c in context.cookies()
                if any(
                    c["name"].startswith(prefix) or c["name"] == name
                    for prefix, name in [("_yfx_session", "wzws_sessionid")]
                )
            }
            browser.close()
            return cached_cookies.copy()
    except Exception:  # noqa: BLE001
        return {}


def clear_cookies() -> None:  # noqa: D103
    global cached_cookies  # noqa: PLW0602
    cached_cookies.clear()


@functools.lru_cache(maxsize=1)
def create_session() -> requests.Session:  # noqa: D103
    session = requests.Session()
    adapter = HTTPAdapter(
        max_retries=Retry(
            total=4,
            backoff_factor=1,
            status_forcelist=frozenset({429, 500, 502, 503, 504}),
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
        ),
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        **HEADERS_TEMPLATE,
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    })
    session.trust_env = False
    return session


def request(method: str, url: str, **kwargs: Any) -> requests.Response:  # noqa: ANN401, D103
    session = create_session()
    for attempt in range(1, 5):
        try:
            response = session.request(method, url, timeout=HTTP_TIMEOUT, **kwargs)
            response.raise_for_status()
            return response  # noqa: TRY300
        except requests.exceptions.RequestException as e:  # noqa: PERF203
            if attempt == 4:  # noqa: PLR2004
                msg = f"Failed to {method.upper()} {url} after 4 attempts"
                raise ConnectionError(msg) from e
            time.sleep(DEF_INIT_DELAY * (2 ** (attempt - 1)) * (0.8 + 0.4 * random.random()))  # noqa: S311
    return None


def fetch_api(type_id: int, page: int) -> APIRESULT:  # noqa: D103
    for attempt in range(3):
        result = _fetch_api(type_id, page)
        if "error" not in result or "Anti-bot detection" not in result.get("error", ""):
            return result
        if attempt < 2:  # noqa: PLR2004
            clear_cookies()
            time.sleep(ANTI_BOT_DELAY * (attempt + 1))
        else:
            return {"result": {"totalSizes": 0, "data": []}, "error": "Anti-bot detection, max retries exceeded"}
    return {"result": {"totalSizes": 0, "data": []}, "error": "Max retries exceeded"}


def _fetch_api(type_id: int, page: int) -> APIRESULT:  # noqa: C901, PLR0911
    payload = {**API_PAYLOAD_BASE, "pageNum": page, "pageSize": API_PAGE_SIZE}

    if type_id == 0:
        payload.update({"searchRange": 0, "searchType": 1, "flfgCodeId": []})
    else:
        payload.update({"searchRange": 1, "searchType": 2, "flfgCodeId": get_flfg_code_id(type_id)})

    headers = {
        **HEADERS_TEMPLATE,
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://flk.npc.gov.cn",
        "Referer": "https://flk.npc.gov.cn/search",
    }

    if session_cookies := get_cookies():
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in session_cookies.items())

    try:
        response = request("POST", SEARCH_LIST_URL, json=payload, headers=headers)

        if not (content := response.content.strip()):
            return {"result": {"totalSizes": 0, "data": []}, "error": "Empty response"}

        text = response.text.strip()
        if text.startswith(HTML_DETECTION_PATTERNS):
            if "function(" in text and any(pattern in text for pattern in ANTIBOT_JS_PATTERNS):
                return {"result": {"totalSizes": 0, "data": []}, "error": "Anti-bot detection"}
            return {"result": {"totalSizes": 0, "data": []}, "error": "HTML response instead of JSON"}

        try:
            result = orjson.loads(content)
        except orjson.JSONDecodeError as e:
            clear_cookies()
            return {"result": {"totalSizes": 0, "data": []}, "error": f"JSON decode error: {e}"}

        if "rows" in result:
            data_items = [
                {
                    "id": item.get("bbbs"),
                    "title": item.get("title"),
                    "url": None,
                    "office": item.get("zdjgName"),
                    "type": item.get("flxz"),
                    "status": item.get("sxx"),
                    "publish": item.get("gbrq"),
                    "expiry": item.get("sxrq"),
                    "_raw": item,
                }
                for item in result.get("rows", [])
            ]
            return {"result": {"data": data_items, "totalSizes": result.get("total", 0)}}
        if "result" in result:
            return result
        return {"result": {"data": [], "totalSizes": 0}}  # noqa: TRY300

    except (ConnectionError, requests.exceptions.RequestException) as e:
        if any(code in str(e) for code in ("401", "403", "Unauthorized")):
            clear_cookies()
        return {"result": {"totalSizes": 0, "data": []}, "error": str(e)}


def fetch_url(bbbs_id: str, format_type: str = "docx") -> str | None:  # noqa: D103, PLR0911
    headers = {**HEADERS_TEMPLATE, "Referer": f"https://flk.npc.gov.cn/detail?id={bbbs_id}"}

    if session_cookies := get_cookies():
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in session_cookies.items())

    download_url = f"{SINGLE_DOWNLOAD_URL}?format={format_type}&bbbs={bbbs_id}"

    try:
        response = request("GET", download_url, headers=headers)

        if response.status_code == 302 or "Location" in response.headers:  # noqa: PLR2004
            return response.headers.get("Location")

        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                result = orjson.loads(response.content)
                if isinstance(result, dict) and result.get("code") == 200 and result.get("msg") == "Success":  # noqa: PLR2004
                    if data := result.get("data", {}):
                        return data.get("url")
                    return None
            except orjson.JSONDecodeError:
                return None

        if response.headers.get("content-type", "").startswith(CONTENT_TYPE_PATTERNS):
            return download_url

        return None  # noqa: TRY300

    except (ConnectionError, requests.exceptions.RequestException):
        return None


def prepare_db_rows(data_list: list[LAWDATA]) -> list[tuple]:  # noqa: D103
    rows = []
    for item in data_list:
        if not item.get("id"):
            continue

        raw_data = item.get("_raw", {})
        bbbs_id = raw_data.get("bbbs") if raw_data else None
        source_api = "new" if raw_data else "old"

        primary_id = bbbs_id or item.get("id")

        row = (
            primary_id,
            item.get("title"),
            item.get("url"),
            item.get("office"),
            item.get("type"),
            item.get("status"),
            item.get("publish"),
            item.get("expiry"),
            0,  # saved
            0,  # parsed
            bbbs_id,
            source_api,
        )
        rows.append(row)
    return rows


class Parser(abc.ABC):  # noqa: D101
    def __init__(self, parser_type: str) -> None:  # noqa: D107
        super().__init__()
        self.parser_type: str = parser_type

    @abc.abstractmethod
    def parse(  # noqa: D102
        self,
        file_path: pathlib.Path,
        title_hint: str,
    ) -> tuple[str, str, list[str]]:
        pass

    def __eq__(self, other: object) -> bool:  # noqa: D105
        if isinstance(other, Parser):
            return self.parser_type == other.parser_type
        return self.parser_type == other if isinstance(other, str) else NotImplemented

    def __hash__(self) -> int:  # noqa: D105
        return hash(self.parser_type)


class Formatter:  # noqa: D101
    @staticmethod
    def _filter_content(content: list[str]) -> list[str]:
        filtered_content: list[str] = []
        is_menu_section: bool = False
        menu_index: int = -1
        pattern: str = ""
        skip_content: bool = False
        pattern_regex: str | None = None

        for i, current_line in enumerate(content):
            processed_line = re.sub(
                r"\s+",
                " ",
                current_line.replace("\u3000", " ").replace("　", " "),
            )

            if menu_index >= 0 and i == menu_index + 1:
                pattern = processed_line
                pattern_regex = next(
                    (r.replace(NUMBER_RE, "一") for r in INDENT_RE if re.match(r, processed_line)),
                    None,
                )
                continue

            if re.match(r"目.*录", processed_line):
                is_menu_section, menu_index = True, i
                continue

            is_menu_section = is_menu_section and not (
                processed_line == pattern
                or (pattern_regex and re.match(pattern_regex, processed_line))
                or (not pattern_regex and re.match(LINE_START, processed_line))
            )

            if i < 40 and re.match(r"公\s*告", processed_line):  # noqa: PLR2004
                skip_content = True

            if not is_menu_section and not skip_content:
                content_line = re.sub(
                    f"^(第{NUMBER_RE}{{1,6}}[条章节篇](?:之{NUMBER_RE}{{1,2}})*)[\\s]*",
                    lambda match: f"{match.group(0).strip()} ",
                    processed_line.strip(),
                )
                if content_line:
                    filtered_content.append(content_line)

            if skip_content and processed_line.startswith(r"法释"):
                skip_content = False

        return filtered_content

    @staticmethod
    def _filter_desc(description: str) -> list[str]:
        cleaned_desc: str = description.strip()

        if not cleaned_desc:
            return []

        if cleaned_desc.startswith(("（", "(")) and cleaned_desc.endswith(("）", ")")):  # noqa: RUF001
            cleaned_desc = cleaned_desc[1:-1].strip()

        cleaned_desc = re.sub(
            r"[()]",
            lambda m: "）" if m.group(0) == ")" else "（",  # noqa: RUF001
            re.sub(r"[ \u3000]+", " ", cleaned_desc),
        ).strip()

        parts: list[str] = re.split(r"(?=\d{4}年\d{1,2}月\d{1,2}日)", cleaned_desc)
        result: list[str] = []

        for part in parts:
            part_text = part.strip()
            if not part_text:
                continue

            if part_text.startswith("根据"):
                result.append("- " + part_text)
            elif re.match(r"\d{4}年\d{1,2}月\d{1,2}日", part_text):
                subparts: list[str] = re.split(r"(?=根据)", part_text)
                for subpart in subparts:
                    clean_subpart = subpart.strip()
                    if not clean_subpart:
                        continue
                    result.append("- " + clean_subpart.replace("起施行", "施行"))
            else:
                result.append("- " + part_text)

        return [line for line in result if line != "- 根据"]

    def format_markdown(  # noqa: D102
        self,
        title: str,
        description: str,
        content: list[str],
    ) -> list[str]:
        heading_map: dict[re.Pattern[str], str] = {
            re.compile(r"序言"): "#### ",
            re.compile(rf"^第{NUMBER_RE}+编"): "## ",
            re.compile(rf"^第{NUMBER_RE}+分编"): "### ",
            re.compile(rf"^第{NUMBER_RE}+章"): "#### ",
            re.compile(rf"^第{NUMBER_RE}+节"): "##### ",
            re.compile(rf"^第{NUMBER_RE}+条"): "###### ",
        }
        condition_pattern: re.Pattern[str] = re.compile(rf"^第{NUMBER_RE}+条")

        filtered_desc_list: list[str] = self._filter_desc(description)
        filtered_content: list[str] = self._filter_content(content)

        if not filtered_content and not filtered_desc_list:
            logger.warning(
                "No content or description left after filtering for: %s",
                title,
            )
            return []

        clean_title: str = title.translate(str.maketrans("()", "（）")).strip()  # noqa: RUF001
        title_lower: str = clean_title.lower()

        output: list[str] = [
            f"# {clean_title}",
            *filtered_desc_list,
            "<!-- INFO END -->",
        ]

        processed_lines: list[str] = [
            self._process_line(
                line.translate(str.maketrans("()", "（）")),  # noqa: RUF001
                heading_map,
                condition_pattern,
            )
            for line in filtered_content
            if line.strip().lower() != title_lower
        ]

        final_output: list[str] = [line for line in [*output, *processed_lines] if line.strip()]

        if len(final_output) < 2:  # noqa: PLR2004
            logger.warning("Markdown output seems minimal for: %s", title)
            return [
                f"# {clean_title}",
                *(filtered_desc_list or []),
                "<!-- INFO END -->",
            ]

        return final_output

    @staticmethod
    def _process_line(
        line: str,
        compiled_patterns: dict[re.Pattern[str], str],
        condition_pattern: re.Pattern[str],
    ) -> str:
        for pattern, header in compiled_patterns.items():
            match: re.Match[str] | None = pattern.match(line)
            if not match:
                continue

            if pattern.pattern == condition_pattern.pattern:
                part: str = match.group().strip()
                content_start_index: int = match.end() + (
                    1 if match.end() < len(line) and line[match.end()] == " " else 0
                )
                content_part: str = line[content_start_index:].strip()
                newline_str: str = "\n\n"
                return f"{header}{part}{(newline_str + content_part) if content_part else ''}"
            return f"{header}{line}"

        return line


class HTML(Parser):  # noqa: D101
    def __init__(self) -> None:  # noqa: D107
        super().__init__("HTML")

    def parse(  # noqa: D102
        self,
        local_file_path: pathlib.Path,
        title_hint: str,
    ) -> tuple[str, str, list[str]]:
        try:
            html_content: str = local_file_path.read_text(encoding="utf-8")
            soup: BeautifulSoup = BeautifulSoup(html_content, features="lxml")
            title: str = getattr(soup.title, "text", "") or title_hint

            content_div: Tag | None = soup.find("div", class_="law-content")
            paragraphs: list[Tag] = content_div.find_all("p") if content_div else soup.find_all("p")

            content: list[str] = [p.get_text().replace("\xa0", " ").strip() for p in paragraphs if p.get_text().strip()]

            content = [text for text in content if not (title and (title.startswith(text) or title.endswith(text)))]

            if not title and content and re.match(r"^中华人民共和国", content[0]):
                title, content = content[0], content[1:]

            description: str = content[0] if content else ""
            content_body: list[str] = content[1:] if len(content) > 1 else []

        except UnicodeDecodeError:
            try:
                return self.parse(pathlib.Path(str(local_file_path)), title_hint)
            except Exception:
                logger.exception("Failed to read HTML file with GBK encoding")
                return "", "", []
        except Exception:
            logger.exception("Error parsing HTML content from %s", local_file_path)
            return "", "", []
        else:
            return title, description, content_body


class Word(Parser):  # noqa: D101
    def __init__(self) -> None:  # noqa: D107
        super().__init__("WORD")

    @staticmethod
    def _iter_doc_blocks(
        parent: _Document | _Cell | _Row,
    ) -> Iterator[Paragraph | Table | None]:
        if isinstance(parent, _Document):
            parent_elem = parent.element.body
        elif isinstance(parent, _Cell):
            parent_elem = parent._tc  # noqa: SLF001
        elif isinstance(parent, _Row):
            parent_elem = parent._tr  # noqa: SLF001
        else:
            msg = f"Unsupported parent type for block iteration: {type(parent)}"
            raise ValueError(  # noqa: TRY004
                msg,
            )

        if parent_elem is None:
            msg = "Parent element is None during block iteration"
            raise ValueError(msg)

        for child in parent_elem.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)
            else:
                yield None

    def parse(  # noqa: D102
        self,
        local_file_path: pathlib.Path,
        title_hint: str,
    ) -> tuple[str, str, list[str]]:
        try:
            document: _Document = Document(str(local_file_path))
            result = self._parse_doc_object(document, title_hint)
        except Exception:
            logger.exception(
                "Failed to open or parse Word document %s",
                local_file_path,
            )
            return "", "", []
        else:
            return result or ("", "", [])

    @staticmethod
    def is_start_line(line: str) -> bool:  # noqa: D102
        return any(re.match(pattern, line) for pattern in LINE_RE)

    def _parse_doc_object(  # noqa: C901
        self,
        document: _Document,
        title: str,
    ) -> tuple[str, str, list[str]] | None:
        if not isinstance(document, _Document):
            logger.error(
                "Invalid object passed to _parse_doc_object. Expected _Document.",
            )
            return None

        description: str = ""
        content: list[str] = []
        is_description: bool = False
        has_description: bool = False

        blocks: list[Paragraph | Table] = [block for block in self._iter_doc_blocks(document) if block is not None]

        def _format_table(row: _Row) -> int:
            cells_text: list[str] = ["\n".join(p.text.strip() for p in cell.paragraphs).strip() for cell in row.cells]
            row_text = f"| {' | '.join(cells_text)} |"
            content.append(row_text)
            return len(cells_text)

        for idx, block in enumerate(blocks):
            if isinstance(block, Table):
                content.append("<!-- TABLE -->")
                if block.rows:
                    column_count = _format_table(block.rows[0])
                    content.append("|" + "|".join(["-----"] * column_count) + "|")
                    for row in block.rows[1:]:
                        _format_table(row)
                content.append("<!-- TABLE END -->")
                continue

            if isinstance(block, Paragraph):
                text = block.text.strip()
                if not text:
                    continue

                if re.match(r"[（(]\d{4}年\d{1,2}月\d{1,2}日", text):  # noqa: RUF001
                    is_description = has_description = True

                if is_description:
                    description += text + "\n"
                elif idx > 0 or not re.match(r"^中华人民共和国", text):
                    content.append(text)

                is_description = (
                    False
                    if is_description
                    and (re.search(r"[）)]$", text) or re.search(r"目.*录", text) or self.is_start_line(text))  # noqa: RUF001
                    else is_description
                )

                has_description = True if not has_description and re.search(r"^法释", text) else has_description
            else:
                logger.warning("Encountered unexpected block type: %s", type(block))

        description = description.strip()
        return title, description, content


def find_doc(legal_title: str, table_name: str, office: str | None = None) -> pathlib.Path | None:  # noqa: C901, D103, PLR0912, PLR0914
    sanitize_re = re.compile(r'[/\\:*?"<>|]')
    ascii_re = re.compile(r"[^\w\s\-_]")

    type_id = next((tid for tid, (code, _) in LAW_TYPES.items() if code == table_name), None)

    api_type = flfg_code_id = None
    if type_id is not None:
        try:
            with contextlib.closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5.0)) as conn:
                conn.executescript("PRAGMA query_only=1; PRAGMA temp_store=MEMORY;")
                row = conn.execute(
                    f'SELECT type, flfgCodeId FROM "{table_name}" WHERE title = ? LIMIT 1',  # noqa: S608
                    (legal_title,),
                ).fetchone()
                if row:
                    api_type, flfg_code_id = row[0], row[1] if len(row) > 1 else None
        except sqlite3.Error:
            pass

    target_dirs = []

    if type_id is not None and (api_type or flfg_code_id):
        target_dirs.append(get_path(api_type or "", type_id, flfg_code_id, office))

    if type_id is None:
        target_dirs.append(BASE_DIR / table_name)
    else:
        type_name = get_type_name(type_id)
        if type_id in frozenset({7, 8, 9, 10}):
            parent_name = get_type_name(2)
            target_dirs.append(
                BASE_DIR / (parent_name or type_name) / type_name if parent_name else BASE_DIR / type_name,
            )
        else:
            base_dir = BASE_DIR / type_name
            target_dirs.append(base_dir)

            if type_id == 6 and office:  # noqa: PLR2004
                region = extract_region_from_office(office)
                if region:
                    target_dirs.extend([
                        base_dir / region,
                        *(
                            base_dir / sanitize_re.sub("_", folder).strip() / region
                            for folder, code in FLFG_CODE_MAP.items()
                            if "地方" in folder and code in frozenset({230, 260, 270, 290, 295, 300, 305, 310})
                        ),
                    ])

    safe_title = sanitize_re.sub("_", legal_title).strip()
    ascii_title = ascii_re.sub("_", legal_title).strip("_ ")

    extensions = frozenset({".docx", ".doc", ".html", ".htm"})

    for target_dir in target_dirs:
        if not target_dir.exists():
            continue

        candidates = (
            target_dir / f"{variant}{ext}" for variant in filter(None, (safe_title, ascii_title)) for ext in extensions
        )

        for path in candidates:
            try:
                stat = path.stat()
                if stat.st_size > 0 and path.is_file():
                    return path
            except OSError:  # noqa: PERF203
                continue

    return None


def parse_doc(  # noqa: C901, D103, PLR0911
    legal_id: str,
    legal_title: str,
    table_name: str,
    office: str | None = None,
) -> str | None:
    logger.info("Parse document: %s (ID: %s)", legal_title, legal_id)

    local_path: pathlib.Path | None = find_doc(legal_title, table_name, office)
    if not local_path:
        logger.warning("Skip parse for '%s': Local file not found.", legal_title)
        return None

    file_extension = local_path.suffix.lower()
    parser: Parser | None = None
    if file_extension in {".docx", ".doc"}:
        parser = Word()
    elif file_extension in {".html", ".htm"}:
        parser = HTML()

    if not parser:
        logger.warning(
            "Skip parse for '%s': Unsupported file extension '%s'.",
            legal_title,
            file_extension,
        )
        return None

    parsed_data: tuple[str, str, list[str]] = parser.parse(local_path, legal_title)
    if not parsed_data or not parsed_data[0]:
        logger.error("Parsing failed for %s at %s.", legal_title, local_path)
        return None

    title, description, content_list = parsed_data

    markdown_lines: list[str] = Formatter().format_markdown(
        title,
        description,
        content_list,
    )
    if not markdown_lines:
        logger.error("Markdown formatting failed for %s.", legal_title)
        return None

    parent_dir = local_path.parent
    parent_dir.mkdir(parents=True, exist_ok=True)

    clean_title = re.sub(r'[/\\:*?"<>|]', "_", title).strip()
    output_path = parent_dir / f"{clean_title}.md"

    try:
        with output_path.open("w", encoding="utf-8") as f:
            f.write("\n\n".join(markdown_lines))
        logger.info("Successfully parsed and saved: %s", output_path.name)
    except OSError:
        logger.exception(
            "Failed to write markdown file %s. Attempting simplified name.",
            output_path,
        )
        simple_output_path = parent_dir / f"{legal_id}.md"
        try:
            with pathlib.Path(simple_output_path).open("w", encoding="utf-8") as f:
                f.write("\n\n".join(markdown_lines))
            logger.info(
                "Successfully parsed and saved with simplified name: %s",
                simple_output_path.name,
            )
        except OSError:
            logger.exception(
                "Failed again to write markdown with simplified filename '%s'",
                simple_output_path.name,
            )
            if output_path.exists():
                output_path.unlink(missing_ok=True)
            if simple_output_path.exists():
                simple_output_path.unlink(missing_ok=True)
            return None
        else:
            return legal_id
    else:
        return legal_id


def download_doc(  # noqa: C901, D103, PLR0911, PLR0912, PLR0915
    legal_id: str,
    legal_title: str,
    table_name: str,
    request_delay: float,
    office: str | None = None,
) -> str | None:
    logger.info("Download attempt: %s (ID: %s)", legal_title, legal_id)

    try:
        bbbs_id = legal_id
        if "%" in legal_id:
            try:
                bbbs_id = base64.b64decode(urllib.parse.unquote(legal_id)).decode("utf-8")
                logger.debug("Decoded %s -> %s", legal_id, bbbs_id)
            except Exception:  # noqa: BLE001, S110
                pass

        if not (doc_url := fetch_url(bbbs_id)):
            logger.warning("API failure: %s (%s)", legal_title, legal_id)
            return None

        ext = pathlib.Path(urllib.parse.urlparse(doc_url).path).suffix.lower() or ".docx"
        if ext == ".cnnone":
            logger.warning("Invalid extension %s: %s", ext, legal_id)
            return legal_id

        type_id = next((tid for tid, (code, _) in LAW_TYPES.items() if code == table_name), None)
        api_type = flfg_code_id = None

        if type_id:
            try:
                with contextlib.closing(sqlite3.connect(DB_PATH, timeout=10.0)) as conn:
                    conn.row_factory = sqlite3.Row
                    if row := conn.execute(
                        f'SELECT type, flfgCodeId FROM "{table_name}" WHERE id = ? LIMIT 1',  # noqa: S608
                        (legal_id,),
                    ).fetchone():
                        api_type, flfg_code_id = (
                            (row["type"] if "type" in row.keys() else None),  # noqa: SIM118
                            (row["flfgCodeId"] if "flfgCodeId" in row.keys() else None),  # noqa: SIM118
                        )
            except sqlite3.Error:
                pass

        target_dir = (
            get_path(api_type or "", type_id, flfg_code_id, office)
            if type_id and (api_type or flfg_code_id)
            else BASE_DIR / (get_type_name(type_id) if type_id else table_name)
        )

        for attempt_dir in [
            target_dir,
            target_dir.parent / (get_type_code(type_id) if type_id else table_name),
            BASE_DIR,
        ]:
            try:
                attempt_dir.mkdir(parents=True, exist_ok=True)
                target_dir = attempt_dir
                break
            except (OSError, UnicodeEncodeError):
                continue

        safe_title = re.sub(r'[/\\:*?"<>|]', "_", legal_title).strip()
        if not safe_title or len(str(target_dir / f"{safe_title}{ext}")) > 255:  # noqa: PLR2004
            safe_title = (
                "".join(c if c.isascii() and (c.isalnum() or c in " -_") else "_" for c in legal_title).strip("_ ")
                or legal_id
            )

        file_path = target_dir / f"{safe_title}{ext}"
        if file_path.exists() and file_path.stat().st_size > 0:
            logger.info("Exists: %s (%d bytes)", file_path, file_path.stat().st_size)
            return legal_id

        response = request("GET", doc_url, stream=True, verify=True)

        for write_path in [file_path, target_dir / f"{legal_id}{ext}"]:
            try:
                with write_path.open("wb") as f:
                    for chunk in response.iter_content(8192):
                        f.write(chunk)
                logger.info("Downloaded: %s (%d bytes)", write_path, write_path.stat().st_size)
                time.sleep(request_delay)
                return legal_id  # noqa: TRY300
            except OSError as e:  # noqa: PERF203
                logger.warning("Write failed %s: %s", write_path, e)
                write_path.unlink(missing_ok=True)
                continue

        return None  # noqa: TRY300

    except (ConnectionError, requests.exceptions.RequestException):
        logger.exception("Network error %s", legal_id)
        return None
    except Exception as e:  # noqa: BLE001
        logger.critical("Fatal error %s: %s", legal_id, e)
        return None


def download_docs(type_id: int, request_delay: float, auto_parse: bool = False) -> None:  # noqa: D103, FBT001, FBT002
    if not (table_name := get_type_code(type_id)):
        logger.error("Invalid type_id %d", type_id)
        return

    type_name = get_type_name(type_id)
    logger.info("Batch download: type %d (%s)", type_id, type_name)

    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, timeout=10.0)) as conn:
            conn.executescript("PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row

            pending = [
                (r[0], r[1], r[2] or "")
                for r in conn.execute(
                    f'SELECT id, title, COALESCE(office, "") FROM "{table_name}" WHERE saved = 0 AND id IS NOT NULL AND title IS NOT NULL',  # noqa: E501, S608
                    timeout=5.0,
                )
            ]

            if not pending:
                logger.info("No pending downloads: %s", type_name)
                auto_parse and parse_docs(type_id, request_delay)
                return

            logger.info("Processing %d items: %s", len(pending), type_name)

            workers = min(max(1, int(CPU_COUNT * MAX_THREADS_RATIO)), 5)
            task_delay = request_delay / workers

            with ThreadPoolExecutor(workers) as executor:
                futures = {
                    executor.submit(download_doc, doc_id, title, table_name, task_delay, office): doc_id
                    for doc_id, title, office in pending
                }

                completed = [doc_id for future, doc_id in futures.items() if future.result()]

            logger.info("%s: %d/%d completed", type_name, len(completed), len(pending))

            if not completed:
                auto_parse and parse_docs(type_id, request_delay)
                return

            with conn:
                conn.executemany(f'UPDATE "{table_name}" SET saved = 1 WHERE id = ?', [(i,) for i in completed])  # noqa: S608

                if auto_parse:
                    parse_targets = [
                        (r[0], r[1], r[2] or "")
                        for r in conn.execute(
                            f'SELECT id, title, COALESCE(office, "") FROM "{table_name}" WHERE id IN ({",".join("?" * len(completed))}) AND parsed = 0',  # noqa: E501, S608
                            completed,
                            timeout=5.0,
                        )
                    ]

                    if parse_targets:
                        parsed = [
                            doc_id
                            for doc_id, title, office in parse_targets
                            if (time.sleep(task_delay / 2), parse_doc(doc_id, title, table_name, office))[1]
                        ]

                        parsed and conn.executemany(
                            f'UPDATE "{table_name}" SET parsed = 1 WHERE id = ?',  # noqa: S608
                            [(i,) for i in parsed],
                        )
                        logger.info("Parsed: %d/%d documents", len(parsed), len(parse_targets))

    except sqlite3.Error:
        logger.exception("Database operation failed: %s", table_name)
    except Exception:
        logger.exception("Download process failed: %s", type_name)


def parse_docs(type_id: int, request_delay: float) -> None:  # noqa: D103
    if not type_id:
        [parse_docs(tid, request_delay) or time.sleep(1) for tid in LAW_TYPES]
        return

    table_name, type_name = get_type_code(type_id), get_type_name(type_id)
    if not table_name:
        logger.error("Invalid type_id %d", type_id)
        return

    logger.info("Batch parsing %d (%s)", type_id, type_name)

    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, timeout=10.0)) as conn:
            conn.row_factory = sqlite3.Row
            rows = [
                (r[0], r[1], r[2] or "")
                for r in conn.execute(
                    f'SELECT id, title, COALESCE(office, "") as office FROM "{table_name}" WHERE saved = 1 AND parsed = 0',  # noqa: E501, S608
                )
                if r[0] and r[1]
            ]
    except sqlite3.Error:
        logger.exception("Query failed: %s", type_name)
        return

    if not rows:
        logger.info("No pending docs: %s", type_name)
        return

    logger.info("Processing %d docs: %s", len(rows), type_name)

    max_workers = min(int(CPU_COUNT * MAX_THREADS_RATIO) or 1, 5)
    delay_per_task = max(0.05, request_delay / (max_workers * 5))

    with ThreadPoolExecutor(max_workers) as executor:
        futures = {executor.submit(parse_doc, *row, table_name): row[0] for row in rows}

        results = []
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                results.append((futures[future], bool(result)))
                time.sleep(delay_per_task)
            except Exception:  # noqa: PERF203
                logger.exception("Parse failed: %s", futures[future])
                results.append((futures[future], False))

    success_ids = [doc_id for doc_id, success in results if success]
    failed = len(results) - len(success_ids)

    logger.info("%s: %d success, %d failed", type_name, len(success_ids), failed)

    if success_ids:
        try:
            with contextlib.closing(sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0)) as conn:
                conn.executescript("PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA busy_timeout=5000")
                with conn:
                    conn.executemany(f'UPDATE "{table_name}" SET parsed = 1 WHERE id = ?', [(i,) for i in success_ids])  # noqa: S608
                logger.info("DB updated: %d docs marked parsed (%s)", len(success_ids), type_name)
        except sqlite3.Error:
            logger.exception("DB update failed (%s): %s", table_name, success_ids)


def crawl_type(  # noqa: C901, D103, PLR0911, PLR0912, PLR0913, PLR0914, PLR0915, PLR0917
    type_id: int,
    download_enabled: bool,  # noqa: FBT001
    start_page: int,
    end_page: int,
    initial_delay: int,
    request_delay: float,
    parse_enabled: bool = False,  # noqa: FBT001, FBT002
) -> None:
    if not type_id:
        return crawl_types(download_enabled, initial_delay, request_delay, parse_enabled)

    type_meta = LAW_TYPES.get(type_id)
    if not type_meta:
        logger.error("Invalid type_id %d", type_id)
        return None

    table_name, type_name = type_meta
    logger.info("Crawling type %d (%s)", type_id, type_name)

    initial_resp = fetch_api(type_id, 1)
    if "error" in initial_resp or not (result := initial_resp.get("result")):
        logger.error("Initial fetch failed for %s: %s", type_name, initial_resp.get("error", "No result"))
        return None

    total_count = result.get("totalSizes", 0)
    if not total_count:
        logger.info("No items for %s", type_name)
        return None

    page_count = (total_count + API_PAGE_SIZE - 1) // API_PAGE_SIZE
    first_page = max(1, start_page) if start_page > 0 else 1
    last_page = min(end_page, page_count) if end_page > 0 else page_count

    if first_page > last_page:
        logger.warning("Invalid page range [%d:%d] for %s", first_page, last_page, type_name)
        return None

    logger.info("Processing %d items (%d pages) for %s", total_count, page_count, type_name)

    all_data = result.get("data", []) if first_page == 1 else []
    pages_to_fetch = range(max(2, first_page), last_page + 1)

    if pages_to_fetch:
        max_workers = min(max(1, int(CPU_COUNT * MAX_THREADS_RATIO)), 4)
        with ThreadPoolExecutor(max_workers) as executor:
            futures = {executor.submit(fetch_api, type_id, page): page for page in pages_to_fetch}

            for future in concurrent.futures.as_completed(futures):
                try:
                    resp = future.result()
                    if "error" not in resp and (resp_result := resp.get("result")):
                        all_data.extend(resp_result.get("data", []))
                        time.sleep(request_delay / max_workers)
                    else:
                        logger.error("Page %d failed: %s", futures[future], resp.get("error", "No result"))
                except Exception:  # noqa: PERF203
                    logger.exception("Page %d exception", futures[future])

    if not all_data:
        logger.warning("No data for %s", type_name)
        return None

    db_records = prepare_db_rows(all_data)
    if not db_records:
        logger.warning("No valid records for %s", type_name)
        return None

    logger.info("Inserting %d records for %s", len(db_records), type_name)

    sql = f'INSERT OR REPLACE INTO "{table_name}" (id, title, url, office, type, status, publish, expiry, saved, parsed, bbbs_id, source_api) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'  # noqa: E501, S608

    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0)) as conn:
            conn.executescript("PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA busy_timeout=5000;")
            with conn:
                cursor = conn.cursor()
                cursor.executemany(sql, db_records)
                changes = conn.total_changes
            logger.info("DB operation complete for %s: %d changes", table_name, changes)
    except sqlite3.Error:
        logger.exception("DB error for %s", table_name)
        return None

    if download_enabled:
        logger.info("Downloading for %s", type_name)
        download_docs(type_id, request_delay, auto_parse=parse_enabled)
    elif parse_enabled:
        logger.info("Parsing for %s", type_name)
        parse_docs(type_id, request_delay)

    logger.info("Completed %s", type_name)
    return None


def crawl_types(  # noqa: D103
    download_enabled: bool,  # noqa: FBT001
    initial_delay: int,
    request_delay: float,
    parse_enabled: bool = False,  # noqa: FBT001, FBT002
) -> None:
    logger.info("Crawling all types")

    max_workers = min(len(LAW_TYPES), max(1, CPU_COUNT >> 1), 2)

    with ThreadPoolExecutor(max_workers) as executor:
        crawl_func = functools.partial(
            crawl_type,
            download_enabled=download_enabled,
            start_page=-1,
            end_page=-1,
            initial_delay=initial_delay,
            request_delay=request_delay,
            parse_enabled=parse_enabled,
        )
        futures = [executor.submit(crawl_func, type_id) for type_id in LAW_TYPES]

        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception:  # noqa: PERF203
                logger.exception("Crawl task failed")

    logger.info("All types complete")


def get_type_id_from_code(type_code: str) -> int | None:  # noqa: D103
    type_id = API_TYPE_TO_ID_MAP.get(type_code)
    if type_id:
        return type_id
    type_code_lower = type_code.lower()
    return next(
        (tid for tid, (_, name) in LAW_TYPES.items() if name.lower() == type_code_lower),
        None,
    )


def check_items(enable_title_check: bool = True) -> dict[int, list[LAWDATA]]:  # noqa: D103, FBT001, FBT002
    logger.info("Checking new items, title_check=%s", "on" if enable_title_check else "off")

    if not LAW_CATEGORIES:
        logger.warning("LAW_CATEGORIES empty, aborting check")
        return {}

    existing_items = _build(enable_title_check)
    if existing_items is None:
        return {}

    existing_ids, existing_titles = existing_items
    logger.info("Cached %d IDs, %d titles", len(existing_ids), len(existing_titles))

    return _paginate(existing_ids, existing_titles, enable_title_check)


def _build(enable_title_check: bool) -> tuple[set[str], set[str]] | None:  # noqa: FBT001
    try:
        with contextlib.closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10.0)) as conn:
            conn.executescript("PRAGMA temp_store=MEMORY;PRAGMA cache_size=-20000;PRAGMA query_only=1")
            cursor = conn.cursor()

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ({})".format(  # noqa: S608
                    ",".join(f"'{cat}'" for cat in LAW_CATEGORIES),
                ),
            )
            valid_categories = [row[0] for row in cursor.fetchall()]

            if not valid_categories:
                logger.warning("No valid category tables found")
                return set(), set()

            schema_map = {}
            for cat in valid_categories:
                cursor.execute(f"PRAGMA table_info({cat})")
                schema_map[cat] = {row[1] for row in cursor.fetchall()}

            id_queries = []
            for cat in valid_categories:
                if "bbbs_id" in schema_map[cat]:
                    id_queries.extend([
                        f'SELECT id FROM "{cat}" WHERE id IS NOT NULL',  # noqa: S608
                        f'SELECT bbbs_id FROM "{cat}" WHERE bbbs_id IS NOT NULL',  # noqa: S608
                    ])
                else:
                    id_queries.append(f'SELECT id FROM "{cat}" WHERE id IS NOT NULL')  # noqa: S608

            existing_ids = set()
            if id_queries:
                unified_query = " UNION ALL ".join(id_queries)
                existing_ids = {row[0] for row in cursor.execute(unified_query) if row[0]}

            existing_titles = set()
            if enable_title_check and valid_categories:
                title_query = " UNION ALL ".join(
                    f'SELECT title FROM "{cat}" WHERE title IS NOT NULL AND title != ""'  # noqa: S608
                    for cat in valid_categories
                )
                existing_titles = {row[0].strip() for row in cursor.execute(title_query) if row[0] and row[0].strip()}

            return existing_ids, existing_titles

    except sqlite3.OperationalError as e:
        if "unable to open" in str(e):
            logger.warning("DB not found, assuming empty: %s", e)
            return set(), set()
        logger.exception("DB operational error")
        return None
    except sqlite3.Error:
        logger.exception("DB error during cache build")
        return None


def _paginate(
    existing_ids: set[str],
    existing_titles: set[str],
    enable_title_check: bool,  # noqa: FBT001
) -> dict[int, list[LAWDATA]]:
    new_items = collections.defaultdict(list)
    page, processed, skipped_existing, skipped_invalid = 1, 0, 0, 0

    while True:
        try:
            response = fetch_api(0, page)
            items = response.get("result", {}).get("data", [])

            if "error" in response or not items:
                logger.error("Page %d failed: %s", page, response.get("error", "empty"))
                break

        except (ConnectionError, requests.exceptions.RequestException):
            logger.exception("Page %d fetch failed", page)
            break

        page_results = _page(items, existing_ids, existing_titles, enable_title_check)
        page_new, page_existing, page_invalid, found_new = page_results

        processed += len(items)
        skipped_existing += page_existing
        skipped_invalid += page_invalid

        for type_id, type_items in page_new.items():
            new_items[type_id].extend(type_items)
            existing_ids.update(item.get("id") for item in type_items if item.get("id"))

        if not found_new and page_existing == len(items):
            logger.info("Page %d: all existing items, stopping pagination", page)
            break

        page += 1

    result = dict(new_items)
    total_new = sum(len(items) for items in result.values())

    logger.info(
        "Discovery complete: processed=%d, existing=%d, invalid=%d, new=%d types=%d",
        processed,
        skipped_existing,
        skipped_invalid,
        total_new,
        len(result),
    )

    return result


def _page(
    items: list[LAWDATA],
    existing_ids: set[str],
    existing_titles: set[str],
    enable_title_check: bool,  # noqa: FBT001
) -> tuple[dict[int, list[LAWDATA]], int, int, bool]:
    page_new = collections.defaultdict(list)
    existing_count = invalid_count = 0
    found_new = False

    for item in items:
        item_id = item.get("id")
        if not item_id:
            continue

        raw_data = item.get("_raw", {})
        bbbs_id = raw_data.get("bbbs") if raw_data else None
        primary_id = bbbs_id or item_id

        if primary_id in existing_ids:
            existing_count += 1
            continue

        if enable_title_check:
            title = item.get("title", "").strip()
            if title and title in existing_titles:
                existing_count += 1
                continue

        api_type = item.get("type")
        if not api_type:
            invalid_count += 1
            continue

        type_id = get_type_id_from_code(api_type)
        if type_id is None:
            invalid_count += 1
            continue

        page_new[type_id].append(item)
        found_new = True

        logger.debug("New: %s [%s] type=%d (primary_id: %s)", item_id, item.get("title", "")[:30], type_id, primary_id)

    return page_new, existing_count, invalid_count, found_new


def process_items(  # noqa: D103
    download_enabled: bool = True,  # noqa: FBT001, FBT002
    initial_delay: int = DEF_INIT_DELAY,
    parse_enabled: bool = True,  # noqa: FBT001, FBT002
    enable_title_check: bool = True,  # noqa: FBT001, FBT002
) -> None:
    logger.info(
        "Processing new items with config: dl=%s, parse=%s, title_check=%s",
        download_enabled,
        parse_enabled,
        enable_title_check,
    )

    if not (new_items := check_items(enable_title_check)):
        return logger.info("No new items found")

    sql_template = 'INSERT OR REPLACE INTO "{}" (id, title, url, office, type, status, publish, expiry, saved, parsed, bbbs_id, source_api) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'  # noqa: E501
    db_pragmas = (
        "PRAGMA journal_mode=WAL;",
        "PRAGMA synchronous=NORMAL;",
        "PRAGMA busy_timeout=5000;",
        "PRAGMA cache_size=-10000;",
        "PRAGMA temp_store=MEMORY;",
    )

    workers = min(len(new_items), max(1, CPU_COUNT >> 1), 2)
    logger.info("Processing %d types with %d workers", len(new_items), workers)

    with ThreadPoolExecutor(workers) as executor:
        futures = {
            executor.submit(
                process_type,
                tid,
                items,
                sql_template,
                db_pragmas,
                download_enabled,
                initial_delay,
                parse_enabled,
            ): tid
            for tid, items in new_items.items()
        }

        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception:  # noqa: PERF203
                logger.exception("Failed processing type %d (%s)", futures[future], get_type_name(futures[future]))

    logger.info("Processing complete")
    return None


def process_existing_items(type_id: int, download_enabled: bool = True, parse_enabled: bool = True) -> None:  # noqa: D103, FBT001, FBT002
    if not (table_name := get_type_code(type_id)):
        return logger.error("Invalid type_id %d", type_id)

    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, timeout=10.0)) as conn:
            conn.row_factory = sqlite3.Row

            if download_enabled:
                to_download = [
                    (r[0], r[1], r[2] or "")
                    for r in conn.execute(
                        f'SELECT id, title, COALESCE(office, "") as office FROM "{table_name}" WHERE saved = 0 AND title IS NOT NULL LIMIT 100',  # noqa: E501, S608
                    ).fetchall()
                    if r[0] and r[1]
                ]

                if to_download:
                    logger.info("Found %d items to download for %s", len(to_download), get_type_name(type_id))
                    download_items(type_id, [item[0] for item in to_download], DEF_REQ_DELAY, auto_parse=parse_enabled)
                    return None

            if parse_enabled:
                to_parse = [
                    (r[0], r[1], r[2] or "")
                    for r in conn.execute(
                        f'SELECT id, title, COALESCE(office, "") as office FROM "{table_name}" WHERE saved = 1 AND parsed = 0 AND title IS NOT NULL LIMIT 100',  # noqa: E501, S608
                    ).fetchall()
                    if r[0] and r[1]
                ]

                if to_parse:
                    logger.info("Found %d items to parse for %s", len(to_parse), get_type_name(type_id))
                    parse_items(type_id, [item[0] for item in to_parse], DEF_REQ_DELAY)
                    return None

            logger.info("No unprocessed items found for %s", get_type_name(type_id))

    except sqlite3.Error:
        logger.exception("DB query failed for %s", get_type_name(type_id))


def process_type(  # noqa: D103, PLR0913, PLR0917
    type_id: int,
    items: list[LAWDATA],
    sql_template: str,
    db_pragmas: tuple[str, ...],
    download_enabled: bool,  # noqa: FBT001
    request_delay: float,
    parse_enabled: bool = False,  # noqa: FBT001, FBT002
) -> None:
    if not (type_code := get_type_code(type_id)):
        return logger.error("Invalid type_id %d", type_id)

    if not (db_records := prepare_db_rows(items)):
        return logger.warning("No valid records for type %s", get_type_name(type_id))

    logger.info("Processing %d items for %s", len(items), get_type_name(type_id))

    try:  # noqa: PLR1702
        with contextlib.closing(
            sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0, check_same_thread=False),
        ) as conn:
            conn.executescript(";".join(db_pragmas))

            with conn:
                try:
                    cursor = conn.cursor()
                    cursor.executemany(sql_template.format(type_code), db_records)
                    changes = cursor.rowcount

                    logger.info(
                        "Attempted to insert %d records for %s, actual changes: %d",
                        len(db_records),
                        get_type_name(type_id),
                        changes,
                    )

                    if not changes:
                        logger.info("No new records inserted - all items already exist in database")
                        existing_unprocessed = cursor.execute(
                            f'SELECT id, title, office FROM "{type_code}" WHERE (saved = 0 OR parsed = 0) AND title IS NOT NULL LIMIT 50',  # noqa: E501, S608
                        ).fetchall()

                        if existing_unprocessed and download_enabled:
                            unprocessed_ids = [r[0] for r in existing_unprocessed if r[0]]
                            logger.info(
                                "Found %d existing unprocessed items, attempting download/parse",
                                len(unprocessed_ids),
                            )
                            download_items(type_id, unprocessed_ids, request_delay, auto_parse=parse_enabled)
                        elif existing_unprocessed and parse_enabled:
                            unprocessed_ids = [r[0] for r in existing_unprocessed if r[0]]
                            logger.info(
                                "Found %d existing unprocessed items, attempting parse",
                                len(unprocessed_ids),
                            )
                            parse_items(type_id, unprocessed_ids, request_delay)
                    else:
                        inserted_ids = [r[0] for r in db_records if r[0]]
                        logger.info("Database operation complete: %d/%d records inserted", changes, len(db_records))

                        if download_enabled:
                            logger.info("Initiating downloads for %d items", len(inserted_ids))
                            download_items(type_id, inserted_ids, request_delay, auto_parse=parse_enabled)
                        elif parse_enabled:
                            logger.info("Parse-only mode for previously downloaded items")
                            parse_items(type_id, inserted_ids, request_delay)

                except (sqlite3.IntegrityError, sqlite3.Error) as e:
                    logger.error("Database operation failed for %s: %s", get_type_name(type_id), e)  # noqa: TRY400

    except sqlite3.Error:
        logger.exception("Critical database error for type %s", type_code)


def download_items(type_id: int, item_ids: list[str], request_delay: float, auto_parse: bool = False) -> None:  # noqa: D103, FBT001, FBT002
    if not (table_name := get_type_code(type_id)):
        return logger.error("Invalid type_id %d", type_id)

    try:
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            items = [
                (r[0], r[1], r[2] or "")
                for r in conn.execute(
                    f'SELECT id, title, COALESCE(office, "") as office FROM "{table_name}" WHERE id IN ({",".join("?" * len(item_ids))})',  # noqa: E501, S608
                    item_ids,
                ).fetchall()
                if r[0] and r[1]
            ]
    except sqlite3.Error:
        return logger.exception("DB query failed for %s", get_type_name(type_id))

    if not items:
        return logger.info("No items found for %s", get_type_name(type_id))

    max_workers = min(max(1, int(CPU_COUNT * MAX_THREADS_RATIO)), 5)

    with ThreadPoolExecutor(max_workers) as executor:
        futures = {
            executor.submit(
                download_doc,
                item[0],  # id
                item[1],  # title
                table_name,
                request_delay / max_workers,
                item[2],  # office
            ): item[0]
            for item in items
        }
        success_ids = [futures[f] for f in concurrent.futures.as_completed(futures) if f.result()]

    logger.info("%s: %d/%d succeeded", get_type_name(type_id), len(success_ids), len(items))

    if success_ids:
        try:
            with sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0) as conn:
                conn.executescript("PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA busy_timeout=5000")
                with conn:
                    conn.executemany(f'UPDATE "{table_name}" SET saved = 1 WHERE id = ?', [(i,) for i in success_ids])  # noqa: S608
                auto_parse and parse_items(type_id, success_ids, request_delay)
        except sqlite3.Error:
            logger.exception("DB update failed for %s: %s", table_name, success_ids)
    return None


def parse_items(type_id: int, item_ids: list[str], request_delay: float) -> None:  # noqa: D103
    if not (item_ids and (table_name := get_type_code(type_id))):
        logger.info("Invalid input: empty items or invalid type_id %d", type_id)
        return

    type_name = get_type_name(type_id)
    logger.info("Parsing %d items for %s", len(item_ids), type_name)

    try:
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            placeholders = ",".join("?" * len(item_ids))
            items_to_parse = [
                (r[0], r[1], r[2] or "")
                for r in conn.execute(
                    f'SELECT id, title, COALESCE(office, "") as office FROM "{table_name}" WHERE id IN ({placeholders}) AND saved = 1',  # noqa: E501, S608
                    item_ids,
                ).fetchall()
                if r[0] and r[1]
            ]
    except sqlite3.Error:
        logger.exception("DB query failed for %s", type_name)
        return

    if not items_to_parse:
        logger.info("No valid items for %s", type_name)
        return

    max_workers = min(int(CPU_COUNT * MAX_THREADS_RATIO) or 1, 5)
    successful_ids, failed = [], 0
    delay_per_worker = max(0.05, request_delay / (max_workers * 5))

    with ThreadPoolExecutor(max_workers) as executor:
        futures = {executor.submit(parse_doc, *item, table_name): item[0] for item in items_to_parse}

        for future in concurrent.futures.as_completed(futures):
            try:
                if future.result():
                    successful_ids.append(futures[future])
                else:
                    failed += 1
                time.sleep(delay_per_worker)
            except Exception:  # noqa: PERF203
                logger.exception("Parse failed: %s", futures[future])
                failed += 1
            finally:
                time.sleep(delay_per_worker)

    logger.info("%s: parsed %d, failed %d", type_name, len(successful_ids), failed)

    if successful_ids:
        try:
            with sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0) as conn:
                conn.executescript("PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA busy_timeout=5000")
                with conn:
                    conn.executemany(
                        f'UPDATE "{table_name}" SET parsed = 1 WHERE id = ?',  # noqa: S608
                        [(i,) for i in successful_ids],
                    )
                logger.info("DB updated: %d docs marked parsed for %s", len(successful_ids), type_name)
        except sqlite3.Error:
            logger.exception("DB update failed for %s: %s", table_name, successful_ids)


def determine_dir(table_name: str, office: str | None = None) -> pathlib.Path | None:  # noqa: D103
    type_id = API_TYPE_TO_ID_MAP.get(table_name)
    if type_id is None:
        fallback = BASE_DIR / table_name
        return fallback if fallback.exists() else None

    type_name = LAW_TYPES[type_id][1]

    if type_id in {7, 8, 9, 10}:
        parent_name = LAW_TYPES[2][1]
        target = BASE_DIR / parent_name / type_name
    else:
        target = BASE_DIR / type_name

        if type_id == 6 and office:  # noqa: PLR2004
            region_match = re.search(r"^(.*?)人民代表大会", office)
            if region_match:
                regional_target = target / region_match.group(1).strip()
                if regional_target.exists():
                    return regional_target

    return (
        target
        if target.exists()
        else next(
            (
                BASE_DIR / code
                for _, (code, name) in LAW_TYPES.items()
                if name == type_name and (BASE_DIR / code).exists()
            ),
            BASE_DIR / table_name if (BASE_DIR / table_name).exists() else None,
        )
    )


def sync_db() -> None:  # noqa: C901, D103
    logger.info("Initiating database-filesystem synchronization")

    pragmas = "PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA busy_timeout=10000;PRAGMA cache_size=-65536"

    sanitize_pattern = re.compile(r'[/\\:*?"<>|]')

    with ThreadPoolExecutor(max_workers=min(len(LAW_TYPES), CPU_COUNT)) as executor:  # noqa: PLR1702

        def sync_table(table_data: tuple[str, str]) -> tuple[str, int]:
            table_name, type_name = table_data

            try:
                with contextlib.closing(sqlite3.connect(DB_PATH, isolation_level=None, timeout=15.0)) as conn:
                    conn.row_factory = sqlite3.Row
                    conn.executescript(pragmas)

                    records = conn.execute(
                        f'SELECT id, title, office, saved, parsed FROM "{table_name}" '  # noqa: S608
                        f'WHERE title IS NOT NULL AND title != ""',
                    ).fetchall()

                    if not records:
                        return type_name, 0

                    updates = []
                    batch_size = 1000

                    for i in range(0, len(records), batch_size):
                        batch = records[i : i + batch_size]
                        batch_updates = []

                        for record in batch:
                            target_dir = determine_dir(table_name, record["office"])
                            if not target_dir:
                                continue

                            clean_title = sanitize_pattern.sub("_", record["title"]).strip()

                            paths = (target_dir / f"{clean_title}.md", target_dir / f"{record['id']}.md")
                            md_exists = any(p.exists() and p.stat().st_size > 0 for p in paths)

                            target_state = 1 if md_exists else 0
                            current_state = (record["saved"], record["parsed"])

                            if (target_state, target_state) != current_state:
                                batch_updates.append((target_state, target_state, record["id"]))

                        updates.extend(batch_updates)

                    if updates:
                        with conn:
                            conn.executemany(
                                f'UPDATE "{table_name}" SET saved = ?, parsed = ? WHERE id = ?',  # noqa: S608
                                updates,
                            )
                        return type_name, len(updates)

                    return type_name, 0

            except sqlite3.Error:
                logger.exception("Sync error for %s", type_name)
                return type_name, -1

        futures = {executor.submit(sync_table, table_data): table_data for table_data in LAW_TYPES.values()}

        total_updates = 0
        for future in concurrent.futures.as_completed(futures):
            type_name, update_count = future.result()
            if update_count > 0:
                logger.info("Synchronized %s records for %s", update_count, type_name)
                total_updates += update_count
            elif update_count == 0:
                logger.debug("No updates required for %s", type_name)
            else:
                logger.warning("Synchronization failed for %s", type_name)

    logger.info("Database synchronization completed. Total updates: %s", total_updates)


def reorg_files() -> None:  # noqa: D103
    logger.info("Starting file reorganization with classification")

    valid_types = [(tid, tn, tt) for tid, (tn, tt) in LAW_TYPES.items() if tid not in {7, 8, 9, 10}]

    with ThreadPoolExecutor(max_workers=min(len(valid_types), CPU_COUNT)) as executor:
        futures = [executor.submit(reorg_files_by_type, tid, tn, tt) for tid, tn, tt in valid_types]
        concurrent.futures.wait(futures)

    logger.info("File reorganization complete")


def reorg_files_by_type(type_id: int, table_name: str, type_name: str) -> None:  # noqa: C901, D103, PLR0912, PLR0914
    main_dir = BASE_DIR / type_name
    if not main_dir.is_dir():
        return

    with contextlib.closing(sqlite3.connect(DB_PATH, timeout=10.0)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                f'SELECT title, type, flfgCodeId, office FROM "{table_name}" WHERE saved = 1 AND title IS NOT NULL',  # noqa: S608
            ).fetchall()
        except sqlite3.Error:
            logger.exception("Database error fetching information for type %s", type_name)
            return

    if not rows:
        return

    file_map = {}
    for row in rows:
        title, api_type, flfg_code_id, office = (
            row["title"],
            row["type"] if "type" in row.keys() else None,  # noqa: SIM118
            row["flfgCodeId"] if "flfgCodeId" in row.keys() else None,  # noqa: SIM118
            row["office"] if "office" in row.keys() else None,  # noqa: SIM118
        )
        safe_title = re.sub(r'[/\\:*?"<>|]', "_", title).strip()

        entry = (api_type, flfg_code_id, office)
        file_map[safe_title] = entry

        ascii_title = "".join(c if c.isascii() and (c.isalnum() or c in " -_") else "_" for c in title).strip("_ ")
        if ascii_title and ascii_title != safe_title:
            file_map[ascii_title] = entry

    moved, skipped, failed = 0, 0, 0
    created_dirs = {}

    for file_path in main_dir.glob("*.*"):
        if not file_path.is_file():
            continue

        entry = file_map.get(file_path.stem)
        if not entry:
            skipped += 1
            continue

        api_type, flfg_code_id, office = entry
        target_dir = get_path(api_type or "", type_id, flfg_code_id, office)

        if target_dir not in created_dirs:
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                created_dirs[target_dir] = True
            except OSError:
                created_dirs[target_dir] = False

        if not created_dirs[target_dir]:
            failed += 1
            continue

        target_path = target_dir / file_path.name
        if target_path.exists():
            skipped += 1
        else:
            try:
                file_path.rename(target_path)
                moved += 1
            except OSError:
                failed += 1

    logger.info(
        "Reorganization complete for %s. Moved: %d, Skipped: %d, Failed: %d",
        type_name,
        moved,
        skipped,
        failed,
    )


if __name__ == "__main__":
    _p = argparse.ArgumentParser(
        description="National Database of Laws and Regulations",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _ops = [
        (
            "-t",
            "--type",
            {
                "type": int,
                "default": 0,
                "help": f"Law type ID to process. Available: {list(LAW_TYPES.keys())}",
            },
        ),
        (
            "-r",
            "--reorganize",
            {
                "action": "store_true",
                "help": "Reorganization with categories",
            },
        ),
        (
            "-d",
            "--download",
            {
                "action": "store_true",
                "help": "Download documents",
            },
        ),
        (
            "-p",
            "--parse",
            {
                "action": "store_true",
                "help": "Parse documents to Markdown",
            },
        ),
        (
            "-c",
            "--check",
            {
                "action": "store_true",
                "help": "Workflow: detect, download, parse, reorganize",
            },
        ),
        (
            "-s",
            "--sync",
            {
                "action": "store_true",
                "help": "Sync database status with files",
            },
        ),
        (
            "--update-schema",
            None,
            {
                "action": "store_true",
                "help": "Update database schema",
            },
        ),
        (
            "--refresh-cookies",
            None,
            {"action": "store_true", "help": "Refresh session cookies"},
        ),
        (
            "--no-title-check",
            None,
            {
                "action": "store_true",
                "help": "Disable title-based duplicate detection",
            },
        ),
    ]
    [_p.add_argument(*(_op[:2] if _op[1] else (_op[0],)), **_op[2]) for _op in _ops]

    _a = _p.parse_args()
    _st = time.monotonic()
    _ec = 0

    try:
        with contextlib.ExitStack():
            logger.info(
                "Starting. Type=%s, Reorganize=%s, Check=%s, Download=%s, Parse=%s, Delay=%.2fs",
                _a.type,
                _a.reorganize,
                _a.check,
                _a.download,
                _a.parse,
                DEF_REQ_DELAY,
            )

            initialize_database()

            _a.refresh_cookies and (clear_cookies(), get_cookies())

            _mode_map = {
                "reorganize": (
                    reorg_files,
                    "File reorganization",
                ),
                "update_schema": (update_schema, "Database schema update"),
                "sync": (sync_db, "File-database synchronization"),
            }

            for _attr, (_func, _msg) in _mode_map.items():
                if getattr(_a, _attr, False):
                    logger.info("Mode: %s", _msg)
                    _func()
                    break
            else:
                if _a.check:
                    logger.info("Mode: Complete workflow")
                    _steps = [
                        (
                            "Processing new items",
                            lambda: process_items(
                                download_enabled=True,
                                initial_delay=int(DEF_REQ_DELAY),
                                parse_enabled=True,
                                enable_title_check=not _a.no_title_check,
                            ),
                        ),
                        (
                            "Downloading remaining documents",
                            lambda: [
                                process_existing_items(_tid, download_enabled=True, parse_enabled=True)
                                for _tid in (LAW_TYPES if _a.type == 0 else [_a.type])
                            ][-1]
                            if _a.type != 0
                            else [
                                process_existing_items(_tid, download_enabled=True, parse_enabled=True)
                                for _tid in LAW_TYPES
                            ][-1]
                            if LAW_TYPES
                            else None,
                        ),
                        (
                            "Parsing remaining documents",
                            lambda: [
                                process_existing_items(_tid, download_enabled=False, parse_enabled=True)
                                for _tid in (LAW_TYPES if _a.type == 0 else [_a.type])
                            ][-1]
                            if _a.type != 0
                            else [
                                process_existing_items(_tid, download_enabled=False, parse_enabled=True)
                                for _tid in LAW_TYPES
                            ][-1]
                            if LAW_TYPES
                            else None,
                        ),
                        ("Reorganizing files", reorg_files),
                    ]
                    for _i, (_desc, _step) in enumerate(_steps):
                        logger.info("Step %d: %s", _i + 1, _desc)
                        _step()
                elif (_a.download, _a.parse) == (True, False):
                    logger.info("Mode: Download only")
                    download_docs(type_id=_a.type, request_delay=DEF_REQ_DELAY)
                elif (_a.download, _a.parse) == (False, True):
                    logger.info("Mode: Parse only")
                    parse_docs(type_id=_a.type, request_delay=DEF_REQ_DELAY)
                elif (_a.download, _a.parse) == (True, True):
                    logger.info("Mode: Download and parse")
                    download_docs(type_id=_a.type, request_delay=DEF_REQ_DELAY, auto_parse=True)
                else:
                    logger.info("Mode: Metadata crawl")
                    crawl_type(
                        type_id=_a.type,
                        download_enabled=_a.download,
                        start_page=-1,
                        end_page=-1,
                        initial_delay=DEF_INIT_DELAY,
                        request_delay=DEF_REQ_DELAY,
                        parse_enabled=_a.parse,
                    )

            logger.info("Completed in %.2f seconds", time.monotonic() - _st)

    except KeyboardInterrupt:
        logger.warning("User cancelled")
        _ec = 130
    except SystemExit as e:
        logger.critical("Exit code %s: %s", e.code, str(e), exc_info=False)
        _ec = e.code if isinstance(e.code, int) else 1
    except Exception as e:
        logger.critical("Fatal error: %s", e, exc_info=True)
        _ec = 1
    finally:
        logging.shutdown()
        sys.exit(_ec)
