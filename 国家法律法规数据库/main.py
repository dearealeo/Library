import argparse
import concurrent.futures
import contextlib
import logging
import os
import random
import re
import sqlite3
import sys
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from functools import lru_cache, partial
from itertools import chain
from pathlib import Path
from typing import (
    Any,
    Dict,
    Final,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
    TypeAlias,
    Union,
)

import orjson
import requests
from bs4 import BeautifulSoup, Tag
from docx import Document
from docx.document import Document as _Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell, _Row
from docx.text.paragraph import Paragraph
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DbRow: TypeAlias = Tuple[
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[int],
    Optional[str],
    Optional[str],
    int,
    int,
]
ApiResult: TypeAlias = Dict[str, Any]
LawData: TypeAlias = Dict[str, Any]


"""
Constants
"""


BASE_DIR: Final[Path] = Path(__file__).resolve().parent
DB_PATH: Final[Path] = BASE_DIR / "database.db"


CPU_COUNT: Final[int] = os.cpu_count() or 1
DEF_INIT_DELAY: Final[int] = 2
DEF_REQ_DELAY: Final[float] = 3.0
MAX_THREADS_RATIO: Final[float] = 1.0
API_PAGE_SIZE: Final[int] = 10
HTTP_TIMEOUT: Final[Tuple[float, float]] = (3.05, 10.0)


# ID -> (code, name)
LAW_TYPES: Final[Dict[int, Tuple[str, str]]] = {
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
LAW_CATEGORIES: Final[Tuple[str, ...]] = tuple(
    details[0] for details in LAW_TYPES.values()
)


_BUILD_TYPE_MAP: Dict[str, int] = {
    "宪法": 1,
    "法律": 7,
    "法律解释": 8,
    "有关法律问题和重大问题的决定": 9,
    "修改、废止的决定": 10,
    "行政法规": 3,
    "监察法规": 4,
    "司法解释": 5,
    "地方性法规": 6,
}
API_TYPE_TO_ID_MAP: Final[Dict[str, int]] = {
    **{code: type_id for type_id, (code, _) in LAW_TYPES.items()},
    **_BUILD_TYPE_MAP,
}
del _BUILD_TYPE_MAP


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
}
METADATA_SCHEMA: Final[Mapping[str, str]] = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL",
    "key": "TEXT NOT NULL UNIQUE",
    "value": "TEXT NOT NULL",
}

LIST_PAGE_URL: Final[str] = (
    "https://flk.npc.gov.cn/api/?type=all&searchType=title%3Bvague&sortTr=f_bbrq_s%3Bdesc&sort=true&page=1&size=10"
)


NUMBER_RE: Final[str] = r"[一二三四五六七八九十零百千万\d]"
INDENT_RE: Final[List[str]] = [
    r"序言",
    rf"^第{NUMBER_RE}+编",
    rf"^第{NUMBER_RE}+分编",
    rf"^第{NUMBER_RE}+章",
    rf"^第{NUMBER_RE}+节",
    r"^([一二三四五六七八九十零百千万]+、.{1,15})[^。；：]$",
]
LINE_RE: Final[List[str]] = INDENT_RE + [rf"^第{NUMBER_RE}+条"]
DESC_REMOVE_PATTERNS: Final[Tuple[str, ...]] = (
    r"^（",
    r"^\(",
    r"）$",
    r"\)$",
    r"^根据",
    r"^自",
)
LINE_START: Final[str] = (
    rf"""^({"|".join(f"({pattern.replace(NUMBER_RE, '一')})" for pattern in (p for p in LINE_RE if "节" not in p))})"""
)


"""
Logging
"""


log_formatter: logging.Formatter = logging.Formatter(
    "%(asctime)s | %(process)d - %(processName)s | %(thread)d - %(threadName)s | %(taskName)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(pathname)s | %(message)s",
    "%Y-%m-%d %H:%M:%S,%f %z",
)
log_handler: logging.StreamHandler[Any] = logging.StreamHandler()
log_handler.setFormatter(log_formatter)
logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(log_handler)
logger.propagate = False


"""
Utility Functions
"""


@lru_cache(maxsize=len(LAW_TYPES))
def get_type_code(type_id: int) -> str:
    return LAW_TYPES.get(type_id, ("", ""))[0]


@lru_cache(maxsize=len(LAW_TYPES))
def get_type_name(type_id: int) -> str:
    return LAW_TYPES.get(type_id, ("", ""))[1]


@lru_cache(maxsize=len(LAW_TYPES))
def get_api_url(type_id: int) -> str:
    type_code = get_type_code(type_id)
    if not type_code:
        raise ValueError(f"Invalid type_id: {type_id}")
    return f"https://flk.npc.gov.cn/api/?type={type_code}&searchType=title%3Bvague&sortTr=f_bbrq_s%3Bdesc&sort=true"


def create_table_sql(table_name: str, schema: Mapping[str, str]) -> str:
    return f'CREATE TABLE IF NOT EXISTS "{table_name}" ({", ".join(f"{k} {v}" for k, v in schema.items())});'


"""
Database
"""


def initialize_database() -> None:
    logger.info("Initialize database at %s.", DB_PATH)

    create_info_table: str = create_table_sql("info", METADATA_SCHEMA)
    create_law_tables: str = "\n".join(
        map(
            lambda category: create_table_sql(category, LAW_TABLE_SCHEMA),
            LAW_CATEGORIES,
        )
    )
    all_create_sql: str = f"{create_info_table};\n{create_law_tables};"

    metadata_to_insert: List[Tuple[str, str]] = [("init_complete", "true")] + list(
        map(
            lambda item: (f"law_type_{item[0]}", f"{item[1][0]}:{item[1][1]}"),
            LAW_TYPES.items(),
        )
    )
    insert_metadata_sql: str = "INSERT OR IGNORE INTO info (key, value) VALUES (?, ?);"

    try:
        with closing(
            sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0)
        ) as conn:
            conn.executescript(
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA cache_size=-10000;"
                "PRAGMA temp_store=MEMORY;"
                "PRAGMA foreign_keys=ON;"
                "PRAGMA busy_timeout=5000;"
            )

            with conn:
                logger.debug("Execute database schema creation script.")
                conn.executescript(all_create_sql)
                logger.debug("Insert initial metadata.")
                conn.executemany(insert_metadata_sql, metadata_to_insert)

        logger.info("Database initialization complete.")
    except sqlite3.Error as e:
        logger.critical("Database initialization failed: %s", e, exc_info=True)
        raise SystemExit(f"Database error: {e}") from e


"""
HTTP
"""


@lru_cache(maxsize=1)
def create_http_session() -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=frozenset({429, 500, 502, 503, 504}),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Connection": "keep-alive",
        }
    )
    session.trust_env = False
    # Optional: Hook to raise exceptions for non-2xx status codes *after* retries
    # session.hooks = {'response': lambda r, *args, **kwargs: r.raise_for_status()}
    logger.debug("Create shared HTTP session with retry strategy.")
    return session


def perform_request(method: str, url: str, **kwargs: Any) -> requests.Response:
    session = create_http_session()
    attempt, max_attempts = 0, 4

    while attempt < max_attempts:
        attempt += 1
        try:
            response = session.request(method, url, timeout=HTTP_TIMEOUT, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            last_exception = e
            logger.warning(
                "Request attempt %d/%d failed for %s %s: %s",
                attempt,
                max_attempts,
                method.upper(),
                url,
                e,
            )
            if attempt < max_attempts:
                wait_time = (
                    DEF_INIT_DELAY
                    * (2 ** (attempt - 1))
                    * (0.8 + 0.4 * random.random())
                )
                logger.info("Retry request in %.2f seconds.", wait_time)
                time.sleep(wait_time)
            else:
                logger.error(
                    "Request failed after %d attempts: %s %s",
                    max_attempts,
                    method.upper(),
                    url,
                )
                raise ConnectionError(
                    f"Failed to {method.upper()} {url} after {max_attempts} attempts"
                ) from last_exception

    raise RuntimeError("Unexpected exit from request loop")


def fetch_api_data(base_url: str, page: int) -> ApiResult:
    timestamp_ms = int(time.time() * 1000)
    url = f"{base_url}&page={page}&size={API_PAGE_SIZE}&_={timestamp_ms}"
    logger.debug("Fetch API data from URL: %s", url)

    try:
        response = perform_request("GET", url)
        result: ApiResult = orjson.loads(response.content)
        logger.debug(
            "Fetched page %d, received %d items.",
            page,
            len(result.get("result", {}).get("data", [])),
        )
        return result
    except (
        orjson.JSONDecodeError,
        ConnectionError,
        requests.exceptions.RequestException,
    ) as e:
        logger.error(
            "Failed to fetch or parse API data from page %d: %s", page, e, exc_info=True
        )
        return {"result": {"totalSizes": 0, "data": []}, "error": str(e)}


def fetch_document_path(legal_id: str) -> Optional[str]:
    base_url = "https://flk.npc.gov.cn"
    api_detail_url = f"{base_url}/api/detail"
    payload = {"id": legal_id}
    logger.debug("Fetch document path for ID: %s", legal_id)

    try:
        response = perform_request("POST", api_detail_url, data=payload)
        result: ApiResult = orjson.loads(response.content)

        body = result.get("result", {}).get("body", [])
        if body and isinstance(body, list) and body[0] and isinstance(body[0], dict):
            path: Optional[str] = body[0].get("path")
            if path:
                logger.debug("Successfully retrieved document path: %s", path)
                return path
            else:
                logger.warning(
                    "No 'path' found in API response body for ID %s. Body: %s",
                    legal_id,
                    body[0],
                )
        else:
            logger.warning(
                "Unexpected API response structure for ID %s: %s", legal_id, result
            )
        return None
    except (
        orjson.JSONDecodeError,
        ConnectionError,
        requests.exceptions.RequestException,
        IndexError,
        KeyError,
    ) as e:
        logger.error(
            "Failed to fetch or parse document path for ID %s: %s",
            legal_id,
            e,
            exc_info=True,
        )
        return None


def prepare_db_rows(data_list: List[LawData]) -> List[DbRow]:
    return [
        (
            item.get("id"),
            item.get("title"),
            item.get("url"),
            item.get("office"),
            item.get("type"),
            item.get("status"),
            item.get("publish"),
            item.get("expiry"),
            0,
            0,
        )
        for item in data_list
        if item.get("id")
    ]


"""
Parser
"""


class Parser(ABC):

    def __init__(self, parser_type: str) -> None:
        super().__init__()
        self.parser_type: str = parser_type

    @abstractmethod
    def parse(self, file_path: Path, title_hint: str) -> Tuple[str, str, List[str]]:
        pass

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Parser):
            return self.parser_type == other.parser_type
        return self.parser_type == other if isinstance(other, str) else NotImplemented

    def __hash__(self) -> int:
        return hash(self.parser_type)


class Formatter:
    @staticmethod
    def _filter_content(content: List[str]) -> List[str]:
        filtered_content: List[str] = []
        is_menu_section: bool = False
        menu_index: int = -1
        pattern: str = ""
        skip_content: bool = False
        pattern_regex: Optional[str] = None

        for i, line in enumerate(content):
            line = re.sub(r"\s+", " ", line.replace("\u3000", " ").replace("　", " "))

            if menu_index >= 0 and i == menu_index + 1:
                pattern = line
                pattern_regex = next(
                    (
                        r.replace(NUMBER_RE, "一")
                        for r in INDENT_RE
                        if re.match(r, line)
                    ),
                    None,
                )
                continue

            if re.match(r"目.*录", line):
                is_menu_section, menu_index = True, i
                continue

            is_menu_section = is_menu_section and not (
                line == pattern
                or (pattern_regex and re.match(pattern_regex, line))
                or (not pattern_regex and re.match(LINE_START, line))
            )

            if i < 40 and re.match(r"公\s*告", line):
                skip_content = True

            if not is_menu_section and not skip_content:
                content_line = re.sub(
                    f"^(第{NUMBER_RE}{{1,6}}[条章节篇](?:之{NUMBER_RE}{{1,2}})*)[\\s]*",
                    lambda match: f"{match.group(0).strip()} ",
                    line.strip(),
                )
                if content_line:
                    filtered_content.append(content_line)

            if skip_content and re.match(r"法释", line):
                skip_content = False

        return filtered_content

    @staticmethod
    def _filter_desc(description: str) -> List[str]:
        cleaned_desc: str = description.strip()

        if not cleaned_desc:
            return []

        if cleaned_desc.startswith(("（", "(")) and cleaned_desc.endswith(("）", ")")):
            cleaned_desc = cleaned_desc[1:-1].strip()

        cleaned_desc = re.sub(
            r"[()]",
            lambda m: "）" if m.group(0) == ")" else "（",
            re.sub(r"[ \u3000]+", " ", cleaned_desc),
        ).strip()

        parts: List[str] = re.split(r"(?=\d{4}年\d{1,2}月\d{1,2}日)", cleaned_desc)
        result: List[str] = []

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if part.startswith("根据"):
                result.append("- " + part)
            elif re.match(r"\d{4}年\d{1,2}月\d{1,2}日", part):
                subparts: List[str] = re.split(r"(?=根据)", part)
                for subpart in subparts:
                    subpart = subpart.strip()
                    if not subpart:
                        continue
                    result.append("- " + subpart.replace("起施行", "施行"))
            else:
                result.append("- " + part)

        return [line for line in result if line != "- 根据"]

    def format_markdown(
        self, title: str, description: str, content: List[str]
    ) -> List[str]:
        heading_map: Dict[re.Pattern[str], str] = {
            re.compile(r"序言"): "#### ",
            re.compile(rf"^第{NUMBER_RE}+编"): "## ",
            re.compile(rf"^第{NUMBER_RE}+分编"): "### ",
            re.compile(rf"^第{NUMBER_RE}+章"): "#### ",
            re.compile(rf"^第{NUMBER_RE}+节"): "##### ",
            re.compile(rf"^第{NUMBER_RE}+条"): "###### ",
        }
        condition_pattern: re.Pattern[str] = re.compile(rf"^第{NUMBER_RE}+条")

        filtered_desc_list: List[str] = self._filter_desc(description)
        filtered_content: List[str] = self._filter_content(content)

        if not filtered_content and not filtered_desc_list:
            logger.warning(
                "No content or description left after filtering for: %s", title
            )
            return []

        clean_title: str = title.translate(str.maketrans("()", "（）")).strip()
        title_lower: str = clean_title.lower()

        output: List[str] = [
            f"# {clean_title}",
            *filtered_desc_list,
            "<!-- INFO END -->",
        ]

        processed_lines: List[str] = [
            self._process_line(
                line.translate(str.maketrans("()", "（）")),
                heading_map,
                condition_pattern,
            )
            for line in filtered_content
            if line.strip().lower() != title_lower
        ]

        final_output: List[str] = [
            line for line in [*output, *processed_lines] if line.strip()
        ]

        if len(final_output) < 2:
            logger.warning("Markdown output seems minimal for: %s", title)
            return [
                f"# {clean_title}",
                *(filtered_desc_list if filtered_desc_list else []),
                "<!-- INFO END -->",
            ]

        return final_output

    @staticmethod
    def _process_line(
        line: str,
        compiled_patterns: Dict[re.Pattern[str], str],
        condition_pattern: re.Pattern[str],
    ) -> str:
        for pattern, header in compiled_patterns.items():
            match: Optional[re.Match[str]] = pattern.match(line)
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
            else:
                return f"{header}{line}"

        return line


class HTML(Parser):
    def __init__(self) -> None:
        super().__init__("HTML")

    def parse(
        self, local_file_path: Path, title_hint: str
    ) -> Tuple[str, str, List[str]]:
        try:
            html_content: str = local_file_path.read_text(encoding="utf-8")
            soup: BeautifulSoup = BeautifulSoup(html_content, features="lxml")
            title: str = getattr(soup.title, "text", "") or title_hint

            content_div: Optional[Tag] = soup.find("div", class_="law-content")
            paragraphs: List[Tag] = (
                content_div.find_all("p") if content_div else soup.find_all("p")
            )

            content: List[str] = [
                p.get_text().replace("\xa0", " ").strip()
                for p in paragraphs
                if p.get_text().strip()
            ]

            content = [
                text
                for text in content
                if not (title and (title.startswith(text) or title.endswith(text)))
            ]

            if not title and content and re.match("^中华人民共和国", content[0]):
                title, content = content[0], content[1:]

            description: str = content[0] if content else ""
            content_body: List[str] = content[1:] if len(content) > 1 else []

            return title, description, content_body

        except UnicodeDecodeError:
            try:
                return self.parse(Path(str(local_file_path)), title_hint)
            except Exception as e:
                logger.error(f"Failed to read HTML file with GBK encoding: {e}")
                return "", "", []
        except Exception as e:
            logger.error(
                "Error parsing HTML content from %s: %s",
                local_file_path,
                e,
                exc_info=True,
            )
            return "", "", []


def is_start_line(line: str) -> bool:
    return any(re.match(pattern, line) for pattern in LINE_RE)


class Word(Parser):
    def __init__(self) -> None:
        super().__init__("WORD")

    @staticmethod
    def _iter_doc_blocks(
        parent: Union[_Document, _Cell, _Row]
    ) -> Iterator[Union[Paragraph, Table, None]]:
        if isinstance(parent, _Document):
            parent_elem = parent.element.body
        elif isinstance(parent, _Cell):
            parent_elem = parent._tc
        elif isinstance(parent, _Row):
            parent_elem = parent._tr
        else:
            raise ValueError(
                f"Unsupported parent type for block iteration: {type(parent)}"
            )

        if parent_elem is None:
            raise ValueError("Parent element is None during block iteration")

        for child in parent_elem.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)
            else:
                yield None

    def parse(
        self, local_file_path: Path, title_hint: str
    ) -> Tuple[str, str, List[str]]:
        try:
            document: _Document = Document(str(local_file_path))
            result = self._parse_doc_object(document, title_hint)
            return result if result else ("", "", [])
        except Exception as e:
            logger.error(
                "Failed to open or parse Word document %s: %s",
                local_file_path,
                e,
                exc_info=True,
            )
            return "", "", []

    def _parse_doc_object(
        self, document: _Document, title: str
    ) -> Optional[Tuple[str, str, List[str]]]:
        if not isinstance(document, _Document):
            logger.error(
                "Invalid object passed to _parse_doc_object. Expected _Document."
            )
            return None

        description: str = ""
        content: List[str] = []
        is_description: bool = False
        has_description: bool = False

        blocks: List[Union[Paragraph, Table]] = [
            block for block in self._iter_doc_blocks(document) if block is not None
        ]

        def _format_table_row(row: _Row) -> int:
            cells_text: List[str] = [
                "\n".join(p.text.strip() for p in cell.paragraphs).strip()
                for cell in row.cells
            ]
            row_text = f"| {' | '.join(cells_text)} |"
            content.append(row_text)
            return len(cells_text)

        for idx, block in enumerate(blocks):
            if isinstance(block, Table):
                content.append("<!-- TABLE -->")
                if block.rows:
                    column_count = _format_table_row(block.rows[0])
                    content.append("|" + "|".join(["-----"] * column_count) + "|")
                    for row in block.rows[1:]:
                        _format_table_row(row)
                content.append("<!-- TABLE END -->")
                continue

            if isinstance(block, Paragraph):
                text = block.text.strip()
                if not text:
                    continue

                if re.match(r"[（(]\d{4}年\d{1,2}月\d{1,2}日", text):
                    is_description = has_description = True

                if is_description:
                    description += text + "\n"
                elif idx > 0 or not re.match("^中华人民共和国", text):
                    content.append(text)

                is_description = (
                    False
                    if is_description
                    and (
                        re.search(r"[）)]$", text)
                        or re.search(r"目.*录", text)
                        or is_start_line(text)
                    )
                    else is_description
                )

                has_description = (
                    True
                    if not has_description and re.search("^法释", text)
                    else has_description
                )
            else:
                logger.warning("Encountered unexpected block type: %s", type(block))

        description = description.strip()
        return title, description, content


md_formatter = Formatter()
html_parser = HTML()
word_parser = Word()


def find_local_doc(
    legal_title: str, table_name: str, office: Optional[str] = None
) -> Optional[Path]:
    target_dir: Path = BASE_DIR
    type_id: Optional[int] = next(
        (tid for tid, (code, _) in LAW_TYPES.items() if code == table_name), None
    )

    if type_id is None:
        target_dir = BASE_DIR / table_name
    else:
        type_name = get_type_name(type_id)
        if type_id in {7, 8, 9, 10}:
            try:
                parent_dir_name = get_type_name(2)
                if parent_dir_name:
                    target_dir /= parent_dir_name
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target_dir /= type_name
                else:
                    logger.error("Could not get parent directory name (type 2).")
                    target_dir = BASE_DIR / type_name
            except Exception as e:
                logger.error(
                    "Error creating nested directory structure for type %d: %s",
                    type_id,
                    e,
                )
                target_dir = BASE_DIR / type_name
        else:
            target_dir /= type_name
            if type_id == 6 and office:
                if match := re.compile(r"^(.*?)人民代表大会").search(office):
                    if region_name := match.group(1).strip():
                        try:
                            sub_dir = target_dir / region_name
                            sub_dir.mkdir(parents=True, exist_ok=True)
                            logger.debug(
                                "Using subdirectory for region '%s' under '%s'.",
                                region_name,
                                target_dir.name,
                            )
                            target_dir = sub_dir
                        except Exception as e:
                            logger.error(
                                "Error creating subdirectory for region '%s': %s",
                                region_name,
                                e,
                            )

    if not target_dir.exists():
        logger.warning("Primary target directory %s does not exist.", target_dir)
        fallback_dir_path: Optional[Path] = next(
            (
                target_dir.parent / code
                for tid, (code, name) in LAW_TYPES.items()
                if name == target_dir.name and (target_dir.parent / code).exists()
            ),
            None,
        )
        if fallback_dir_path:
            logger.info("Using fallback directory: %s", fallback_dir_path)
            target_dir = fallback_dir_path
        elif not (BASE_DIR / table_name).exists():
            logger.warning(
                "Neither primary nor fallback directory exists for '%s'. Cannot find file.",
                legal_title,
            )
            return None
        else:
            logger.info("Using base table name directory: %s", BASE_DIR / table_name)
            target_dir = BASE_DIR / table_name

    safe_title = re.sub(r'[/\\:*?"<>|]', "_", legal_title).strip()
    simplified_title = "".join(
        c if c.isascii() and (c.isalnum() or c in (" ", "-", "_")) else "_"
        for c in legal_title
    ).strip("_ ")

    possible_extensions = (".docx", ".doc", ".html", ".htm")

    path_variants: Iterable[Path] = (
        (target_dir / f"{title_variant}{ext}")
        for title_variant in (safe_title, simplified_title)
        if title_variant
        for ext in possible_extensions
    )

    found_path: Optional[Path] = next(
        (path for path in path_variants if path.is_file() and path.stat().st_size > 0),
        None,
    )

    if found_path:
        logger.debug("Found local document for '%s': %s", legal_title, found_path)
        return found_path
    else:
        logger.warning(
            "Could not find local document file for '%s' in %s (checked variants: %s, %s).",
            legal_title,
            target_dir,
            safe_title,
            simplified_title,
        )
        return None


def parse_doc_to_md(
    legal_id: str,
    legal_title: str,
    table_name: str,
    office: Optional[str] = None,
) -> Optional[str]:
    logger.info("Parse document: %s (ID: %s)", legal_title, legal_id)

    local_path: Optional[Path] = find_local_doc(legal_title, table_name, office)
    if not local_path:
        logger.warning("Skip parse for '%s': Local file not found.", legal_title)
        return None

    file_extension = local_path.suffix.lower()
    parser: Optional[Parser] = None
    if file_extension in (".docx", ".doc"):
        parser = word_parser
    elif file_extension in (".html", ".htm"):
        parser = html_parser

    if not parser:
        logger.warning(
            "Skip parse for '%s': Unsupported file extension '%s'.",
            legal_title,
            file_extension,
        )
        return None

    parsed_data: Tuple[str, str, List[str]] = parser.parse(local_path, legal_title)
    if not parsed_data or not parsed_data[0]:
        logger.error("Parsing failed for %s at %s.", legal_title, local_path)
        return None

    title, description, content_list = parsed_data

    markdown_lines: List[str] = md_formatter.format_markdown(
        title, description, content_list
    )
    if not markdown_lines:
        logger.error("Markdown formatting failed for %s.", legal_title)
        return None

    parent_dir = local_path.parent
    parent_dir.mkdir(parents=True, exist_ok=True)

    clean_title = re.sub(r'[/\\:*?"<>|]', "_", title).strip()
    output_path = parent_dir / f"{clean_title}.md"

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\\n\\n".join(markdown_lines))
        logger.info("Successfully parsed and saved: %s", output_path.name)
        return legal_id
    except (IOError, OSError) as e:
        logger.error(
            "Failed to write markdown file %s: %s. Attempting simplified name.",
            output_path,
            e,
        )
        simple_output_path = parent_dir / f"{legal_id}.md"
        try:
            with open(simple_output_path, "w", encoding="utf-8") as f:
                f.write("\\n\\n".join(markdown_lines))
            logger.info(
                "Successfully parsed and saved with simplified name: %s",
                simple_output_path.name,
            )
            return legal_id
        except (IOError, OSError) as e2:
            logger.error(
                "Failed again to write markdown with simplified filename '%s': %s",
                simple_output_path.name,
                e2,
            )
            if output_path.exists():
                output_path.unlink(missing_ok=True)
            if simple_output_path.exists():
                simple_output_path.unlink(missing_ok=True)
            return None


"""
Crawling and Downloading
"""


def download_document(
    legal_id: str,
    legal_title: str,
    table_name: str,
    request_delay: float,
    office: Optional[str] = None,
) -> Optional[str]:
    logger.info("Attempt download: %s (ID: %s)", legal_title, legal_id)

    try:
        doc_path_rel: Optional[str] = fetch_document_path(legal_id)
        if not doc_path_rel:
            logger.warning(
                "Cannot retrieve document path for '%s' (ID: %s). Skip download.",
                legal_title,
                legal_id,
            )
            return None

        doc_path: str = doc_path_rel.lstrip("/")
        doc_url: str = f"https://wb.flk.npc.gov.cn/{doc_path}"
        file_extension: str = Path(doc_path).suffix.lower()

        if file_extension == ".cnnone":
            logger.warning(
                "Document '%s' (ID: %s) has invalid extension '%s'. Skip download, but mark as 'saved' potentially?",
                legal_title,
                legal_id,
                file_extension,
            )
            return legal_id

        target_dir: Path = BASE_DIR
        type_id: Optional[int] = next(
            (tid for tid, (code, _) in LAW_TYPES.items() if code == table_name), None
        )

        if type_id is None:
            logger.error(
                "Cannot determine type_id for table '%s'. Using table name '%s' as directory.",
                table_name,
                table_name,
            )
            target_dir = BASE_DIR / table_name
        else:
            type_name = get_type_name(type_id)
            if type_id in {7, 8, 9, 10}:
                try:
                    parent_dir_name = get_type_name(2)
                    if parent_dir_name:
                        parent_dir = target_dir / parent_dir_name
                        parent_dir.mkdir(parents=True, exist_ok=True)
                        target_dir = parent_dir / type_name
                    else:
                        logger.error("Could not get parent directory name (type 2).")
                        target_dir = BASE_DIR / type_name
                except Exception as e:
                    logger.error(
                        "Error creating nested directory structure for type %d (%s): %s",
                        type_id,
                        type_name,
                        e,
                    )
                    target_dir = BASE_DIR / type_name
            else:
                target_dir /= type_name
                if type_id == 6 and office:
                    region_pattern = re.compile(r"^(.*?)人民代表大会")
                    if match := region_pattern.search(office):
                        if region_name := match.group(1).strip():
                            try:
                                sub_dir = target_dir / region_name
                                sub_dir.mkdir(parents=True, exist_ok=True)
                                logger.debug(
                                    "Using subdirectory for region: %s", region_name
                                )
                                target_dir = sub_dir
                            except Exception as e:
                                logger.error(
                                    "Error creating subdirectory for region '%s': %s",
                                    region_name,
                                    e,
                                )

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            logger.debug("Ensured directory exists: %s", target_dir)
        except (OSError, UnicodeEncodeError) as e:
            logger.error(
                "Error creating directory '%s': %s. Trying fallback.", target_dir, e
            )
            fallback_dir_name = get_type_code(type_id) if type_id else table_name
            target_dir = target_dir.parent / fallback_dir_name
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                logger.info("Using fallback directory: %s", target_dir)
            except (OSError, PermissionError) as e_fallback:
                logger.error(
                    "Fallback directory creation failed '%s': %s. Using base directory.",
                    target_dir,
                    e_fallback,
                )
                target_dir = BASE_DIR
                target_dir.mkdir(exist_ok=True)

        safe_title: str
        try:
            safe_title = re.sub(r'[/\\:*?"<>|]', "_", legal_title).strip()
            _ = str(target_dir / f"{safe_title}{file_extension}")
        except (UnicodeEncodeError, OSError):
            logger.warning(
                "Unicode/OS error constructing filename for '%s'. Using simplified ASCII filename.",
                legal_title,
            )
            safe_title = "".join(
                c if c.isascii() and (c.isalnum() or c in (" ", "-", "_")) else "_"
                for c in legal_title
            ).strip("_ ")
            if not safe_title:
                safe_title = legal_id

        file_path: Path = target_dir / f"{safe_title}{file_extension}"

        if file_path.exists() and file_path.stat().st_size > 0:
            logger.info(
                "Document '%s' already exists at %s (size %d). Skip download.",
                legal_title,
                file_path,
                file_path.stat().st_size,
            )
            return legal_id

        logger.debug("Downloading from %s to %s", doc_url, file_path)
        verify_ssl: bool = True
        if not verify_ssl:
            logger.warning("Disabling SSL verification for downloading %s", doc_url)

        response: requests.Response = perform_request(
            "GET", doc_url, stream=True, verify=verify_ssl
        )

        try:
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(
                "Successfully downloaded document to %s (Size: %d)",
                file_path,
                file_path.stat().st_size,
            )
            time.sleep(request_delay)
            return legal_id
        except (IOError, OSError) as e:
            logger.error(
                "Failed to write document '%s' to %s: %s. Attempting simplified name.",
                legal_title,
                file_path,
                e,
            )
            simple_file_path = target_dir / f"{legal_id}{file_extension}"
            try:
                with open(simple_file_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                logger.info(
                    "Successfully downloaded document to simplified path: %s (Size: %d)",
                    simple_file_path,
                    simple_file_path.stat().st_size,
                )
                time.sleep(request_delay)
                return legal_id
            except (IOError, OSError) as e2:
                logger.error(
                    "Failed again to write document %s with simplified filename '%s': %s",
                    legal_title,
                    simple_file_path.name,
                    e2,
                )
                for path_to_clean in (file_path, simple_file_path):
                    if path_to_clean.exists():
                        path_to_clean.unlink(missing_ok=True)
                return None

    except (ConnectionError, requests.exceptions.RequestException) as e:
        logger.error(
            "Network/Request error during download process for '%s' (ID: %s): %s",
            legal_title,
            legal_id,
            e,
            exc_info=True,
        )
        return None
    except Exception as e:
        logger.critical(
            "Unexpected error downloading document '%s' (ID: %s): %s",
            legal_title,
            legal_id,
            e,
            exc_info=True,
        )
        return None


def download_all_docs(
    type_id: int, request_delay: float, auto_parse: bool = False
) -> None:
    table_name = get_type_code(type_id)
    type_name = get_type_name(type_id)
    if not table_name:
        logger.error("Invalid type_id %d provided for downloading.", type_id)
        return

    logger.info("Start batch download for law type %d (%s).", type_id, type_name)

    rows_to_download: List[Tuple[str, str, str]] = []
    try:
        with closing(sqlite3.connect(DB_PATH, timeout=10.0)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                f'SELECT id, title, office FROM "{table_name}" WHERE saved = 0'
            )
            rows_to_download = [
                (
                    row["id"],
                    row["title"],
                    row["office"] if "office" in row.keys() else "",
                )
                for row in cursor.fetchall()
                if row["id"] and row["title"]
            ]
    except sqlite3.Error as e:
        logger.error(
            "Failed to query database for documents to download (type %s): %s",
            type_name,
            e,
            exc_info=True,
        )
        return

    if not rows_to_download:
        logger.info("No documents require downloading for %s.", type_name)
        if auto_parse:
            logger.info(
                "Proceeding to parse any existing unparsed documents for %s.", type_name
            )
            parse_saved_docs(type_id, request_delay)
        return

    logger.info(
        "Found %d documents to download for %s.", len(rows_to_download), type_name
    )

    successful_ids: List[str] = []
    failed_count: int = 0
    max_workers: int = min(max(1, int(CPU_COUNT * MAX_THREADS_RATIO)), 5)

    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix=f"Download_{table_name}"
    ) as executor:
        download_func = partial(
            download_document,
            table_name=table_name,
            request_delay=request_delay / max_workers,
        )
        future_to_id: Dict[Future[Optional[str]], str] = {
            executor.submit(
                download_func, legal_id=legal_id, legal_title=legal_title, office=office
            ): legal_id
            for legal_id, legal_title, office in rows_to_download
        }

        for future in future_to_id:
            legal_id = future_to_id[future]
            try:
                result_id: Optional[str] = future.result()
                if result_id:
                    successful_ids.append(result_id)
                else:
                    logger.warning("Download task failed for ID: %s", legal_id)
                    failed_count += 1
            except Exception as exc:
                logger.error(
                    "Download task for ID %s generated an exception: %s",
                    legal_id,
                    exc,
                    exc_info=True,
                )
                failed_count += 1

    logger.info(
        "Download process completed for %s. Success/Already Existed: %d, Failed: %d.",
        type_name,
        len(successful_ids),
        failed_count,
    )

    if successful_ids:
        logger.info(
            "Update database: Mark %d documents as saved for %s.",
            len(successful_ids),
            type_name,
        )
        update_sql = f'UPDATE "{table_name}" SET saved = 1 WHERE id = ?'
        try:
            with closing(
                sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0)
            ) as update_conn:
                update_conn.execute("PRAGMA journal_mode=WAL;")
                update_conn.execute("PRAGMA synchronous=NORMAL;")
                update_conn.execute("PRAGMA busy_timeout = 5000;")

                with update_conn:
                    update_conn.executemany(
                        update_sql, [(id_val,) for id_val in successful_ids]
                    )
                logger.info(
                    "Database updated successfully for %d documents marked as saved.",
                    len(successful_ids),
                )

                if auto_parse:
                    logger.info(
                        "Initiating automatic parsing for newly saved documents."
                    )
                    update_conn.row_factory = sqlite3.Row
                    cursor = update_conn.cursor()
                    placeholders = ",".join(["?"] * len(successful_ids))
                    query = f"""
                        SELECT id, title, office
                        FROM "{table_name}"
                        WHERE id IN ({placeholders}) AND saved = 1 AND parsed = 0
                    """
                    cursor.execute(query, successful_ids)
                    docs_to_parse: List[Tuple[str, str, str]] = [
                        (
                            row["id"],
                            row["title"],
                            row["office"] if "office" in row.keys() else "",
                        )
                        for row in cursor.fetchall()
                    ]

                    if docs_to_parse:
                        logger.info(
                            "Found %d newly saved documents to parse.",
                            len(docs_to_parse),
                        )
                        parsed_ids: List[str] = []
                        for doc_id, doc_title, doc_office in docs_to_parse:
                            time.sleep(
                                request_delay / (2 * max_workers)
                                if max_workers > 0
                                else 0.1
                            )
                            if result := parse_doc_to_md(
                                doc_id, doc_title, table_name, doc_office
                            ):
                                parsed_ids.append(result)

                        if parsed_ids:
                            parse_update_sql = (
                                f'UPDATE "{table_name}" SET parsed = 1 WHERE id = ?'
                            )
                            with update_conn:
                                update_conn.executemany(
                                    parse_update_sql,
                                    [(id_val,) for id_val in parsed_ids],
                                )
                            logger.info(
                                "Successfully parsed and marked %d/%d documents.",
                                len(parsed_ids),
                                len(docs_to_parse),
                            )
                        else:
                            logger.info(
                                "No documents were successfully parsed in this batch."
                            )
                    else:
                        logger.info(
                            "No newly saved documents require parsing at this time."
                        )

        except sqlite3.Error as e:
            logger.error(
                "Failed to update database after downloads for table '%s': %s",
                table_name,
                e,
                exc_info=True,
            )
            logger.error(
                "Failed IDs requiring manual check (saved status): %s", successful_ids
            )

    elif auto_parse:
        logger.info(
            "No new documents downloaded, but auto-parse is enabled. Checking for existing unparsed files."
        )
        parse_saved_docs(type_id, request_delay)


def parse_saved_docs(type_id: int, request_delay: float) -> None:
    if type_id == 0:
        for law_type_id in LAW_TYPES:
            parse_saved_docs(law_type_id, request_delay)
            time.sleep(1)
        return

    table_name = get_type_code(type_id)
    type_name = get_type_name(type_id)
    if not table_name:
        logger.error("Invalid type_id %d provided for parsing.", type_id)
        return

    logger.info("Start batch parsing for law type %d (%s).", type_id, type_name)

    rows_to_parse: List[Tuple[str, str, str]] = []
    try:
        with closing(sqlite3.connect(DB_PATH, timeout=10.0)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                f'SELECT id, title, office FROM "{table_name}" WHERE saved = 1 AND parsed = 0'
            )
            rows_to_parse = [
                (
                    row["id"],
                    row["title"],
                    row["office"] if "office" in row.keys() else "",
                )
                for row in cursor.fetchall()
                if row["id"] and row["title"]
            ]
    except sqlite3.Error as e:
        logger.error(
            "Failed to query database for documents to parse (type %s): %s",
            type_name,
            e,
            exc_info=True,
        )
        return

    if not rows_to_parse:
        logger.info("No unparsed documents found for %s.", type_name)
        return

    logger.info("Found %d documents to parse for %s.", len(rows_to_parse), type_name)

    successful_ids: List[str] = []
    failed_count: int = 0
    max_workers: int = min(max(1, int(CPU_COUNT * MAX_THREADS_RATIO)), 5)

    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix=f"Parse_{table_name}"
    ) as executor:
        future_to_id: Dict[Future[Optional[str]], str] = {
            executor.submit(
                parse_doc_to_md, legal_id, legal_title, table_name, office
            ): legal_id
            for legal_id, legal_title, office in rows_to_parse
        }

        for future in concurrent.futures.as_completed(future_to_id):
            legal_id = future_to_id[future]
            try:
                result_id: Optional[str] = future.result()
                if result_id:
                    successful_ids.append(result_id)
                else:
                    failed_count += 1
                time.sleep(
                    max(0.05, request_delay / (max_workers * 5))
                    if max_workers > 0
                    else 0.1
                )
            except Exception as exc:
                logger.error(
                    "Parsing task for ID %s generated an exception: %s",
                    legal_id,
                    exc,
                    exc_info=True,
                )
                failed_count += 1

    logger.info(
        "Parsing process completed for %s. Success: %d, Failed: %d.",
        type_name,
        len(successful_ids),
        failed_count,
    )

    if successful_ids:
        update_sql = f'UPDATE "{table_name}" SET parsed = 1 WHERE id = ?'
        try:
            with closing(
                sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0)
            ) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA busy_timeout = 5000;")
                with conn:
                    conn.executemany(
                        update_sql, [(id_val,) for id_val in successful_ids]
                    )
                logger.info(
                    "Database updated successfully: Marked %d documents as parsed for %s.",
                    len(successful_ids),
                    type_name,
                )
        except sqlite3.Error as e:
            logger.error(
                "Failed to update database after parsing for table '%s': %s",
                table_name,
                e,
                exc_info=True,
            )
            logger.error(
                "Failed IDs requiring manual check (parsed status): %s", successful_ids
            )


def crawl_law_type(
    type_id: int,
    download_enabled: bool,
    start_page: int,
    end_page: int,
    initial_delay: int,
    request_delay: float,
    parse_enabled: bool = False,
) -> None:
    if type_id == 0:
        crawl_all_types(download_enabled, initial_delay, request_delay, parse_enabled)
        return

    type_name = get_type_name(type_id)
    table_name = get_type_code(type_id)
    if not type_name or not table_name:
        logger.error("Invalid law type ID provided: %d. Aborting crawl.", type_id)
        return

    logger.info("Starting crawl for law type %d (%s).", type_id, type_name)

    try:
        api_url: str = get_api_url(type_id)
    except ValueError as e:
        logger.error("Cannot get API URL for type %d: %s. Aborting crawl.", type_id, e)
        return

    logger.info("Fetching initial page to determine total count for %s.", type_name)
    initial_response: ApiResult = fetch_api_data(api_url, 1)
    if "error" in initial_response or not initial_response.get("result"):
        logger.error(
            "Failed to fetch initial page for %s. Error: %s. Aborting crawl.",
            type_name,
            initial_response.get("error", "Unknown API error or structure"),
        )
        return

    try:
        total_count = int(initial_response["result"]["totalSizes"])
        page_count = (total_count + API_PAGE_SIZE - 1) // API_PAGE_SIZE
        logger.info(
            "Found %d items across %d pages for %s.", total_count, page_count, type_name
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.error(
            "Cannot parse total size from initial response for %s: %s. Response: %s. Aborting crawl.",
            type_name,
            e,
            initial_response,
            exc_info=True,
        )
        return

    if total_count == 0:
        logger.info("No items found for %s. Skipping further crawling.", type_name)
        return

    first_page: int = max(1, start_page) if start_page > 0 else 1
    last_page: int = min(end_page, page_count) if end_page > 0 else page_count
    logger.info(
        "Effective crawl range for %s: Pages %d to %d.",
        type_name,
        first_page,
        last_page,
    )

    if first_page > last_page:
        logger.warning(
            "Start page (%d) is after last page (%d) for %s. No pages to crawl.",
            first_page,
            last_page,
            type_name,
        )
        return

    all_api_data: List[LawData] = []
    if (
        first_page == 1
        and "result" in initial_response
        and "data" in initial_response["result"]
    ):
        all_api_data.extend(initial_response["result"]["data"])

    pages_to_fetch: range = range(max(2, first_page), last_page + 1)
    if pages_to_fetch:
        logger.info(
            "Fetching pages %d through %d concurrently for %s...",
            pages_to_fetch.start,
            pages_to_fetch.stop - 1,
            type_name,
        )
        max_workers: int = min(max(1, int(CPU_COUNT * MAX_THREADS_RATIO)), 4)

        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=f"API_{table_name}"
        ) as executor:
            fetch_func = partial(fetch_api_data, api_url)
            future_to_page: Dict[Future[ApiResult], int] = {
                executor.submit(fetch_func, page): page for page in pages_to_fetch
            }

            for future in concurrent.futures.as_completed(future_to_page):
                page = future_to_page[future]
                try:
                    response_data: ApiResult = future.result()
                    if "error" not in response_data and response_data.get("result"):
                        page_data: List[LawData] = response_data["result"].get(
                            "data", []
                        )
                        all_api_data.extend(page_data)
                        time.sleep(
                            request_delay / max_workers
                            if max_workers > 0
                            else request_delay
                        )
                    else:
                        logger.error(
                            "Failed to get valid data for page %d (%s): %s",
                            page,
                            table_name,
                            response_data.get("error", "Unknown structure or error"),
                        )
                except Exception as exc:
                    logger.error(
                        "Fetching page %d (%s) generated an exception: %s",
                        page,
                        table_name,
                        exc,
                        exc_info=True,
                    )

    logger.info(
        "Finished fetching API data for %s. Total items retrieved: %d.",
        type_name,
        len(all_api_data),
    )

    if all_api_data:
        db_records: List[DbRow] = prepare_db_rows(all_api_data)
        logger.info(
            "Prepared %d valid items for database insertion into '%s'.",
            len(db_records),
            table_name,
        )

        if db_records:
            sql = f'INSERT OR IGNORE INTO "{table_name}" (id, title, url, office, type, status, publish, expiry, saved, parsed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
            try:
                with closing(
                    sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0)
                ) as conn:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA synchronous=NORMAL;")
                    conn.execute("PRAGMA busy_timeout = 5000;")
                    with conn:
                        cursor = conn.cursor()
                        cursor.executemany(sql, db_records)
                        changes = conn.total_changes
                    logger.info(
                        "Database operation complete for table '%s'. Processed %d items. DB changes: %d.",
                        table_name,
                        len(db_records),
                        changes,
                    )
            except sqlite3.Error as e:
                logger.error(
                    "Database error saving records for %s: %s",
                    table_name,
                    e,
                    exc_info=True,
                )
        else:
            logger.warning(
                "No valid database records prepared after API fetch for %s.", table_name
            )
    else:
        logger.warning("No data retrieved from API for %s.", table_name)

    if download_enabled:
        logger.info("Initiating document downloads for %s.", type_name)
        download_all_docs(type_id, request_delay, auto_parse=parse_enabled)
    elif parse_enabled:
        logger.info(
            "Download disabled but parse enabled. Initiating parsing of existing documents for %s.",
            type_name,
        )
        parse_saved_docs(type_id, request_delay)
    else:
        logger.info("Download and parse are disabled for %s.", type_name)

    logger.info("Completed crawl process for law type %d (%s).", type_id, type_name)


def crawl_all_types(
    download_enabled: bool,
    initial_delay: int,
    request_delay: float,
    parse_enabled: bool = False,
) -> None:
    logger.info("Starting crawl of ALL law types.")

    max_workers: int = min(len(LAW_TYPES), max(1, CPU_COUNT // 2), 2)
    logger.info(
        "Crawling %d types concurrently with max %d workers.",
        len(LAW_TYPES),
        max_workers,
    )

    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="CrawlType"
    ) as executor:
        crawl_func = partial(
            crawl_law_type,
            download_enabled=download_enabled,
            start_page=-1,
            end_page=-1,
            initial_delay=initial_delay,
            request_delay=request_delay,
            parse_enabled=parse_enabled,
        )
        futures: List[Future[None]] = [
            executor.submit(crawl_func, type_id=type_id) for type_id in LAW_TYPES.keys()
        ]

        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                logger.error("A law type crawl task failed: %s", exc, exc_info=True)

    logger.info("Completed crawl of all law types.")


# def fetch_list_page_items() -> List[LawData]:
#     logger.info("Fetching items from list page API: %s", LIST_PAGE_URL)

#     try:
#         response: requests.Response = perform_request("GET", LIST_PAGE_URL)

#         try:
#             result: ApiResult = orjson.loads(response.content)
#             items: List[LawData] = result.get("result", {}).get("data", [])
#             logger.info(
#                 "Successfully extracted %d items from list page API.", len(items)
#             )
#             return items
#         except (orjson.JSONDecodeError, KeyError) as e:
#             logger.error(
#                 "Failed to parse JSON from list page API response: %s. Raw response snippet: %s",
#                 e,
#                 response.text[:500],
#                 exc_info=True,
#             )
#             return []

#     except (ConnectionError, requests.exceptions.RequestException) as e:
#         logger.error("Failed to fetch list page API: %s", e, exc_info=True)
#         return []


def get_type_id_from_code(type_code: str) -> Optional[int]:
    type_id = API_TYPE_TO_ID_MAP.get(type_code)
    if type_id:
        return type_id
    type_code_lower = type_code.lower()
    return next(
        (
            tid
            for tid, (_, name) in LAW_TYPES.items()
            if name.lower() == type_code_lower
        ),
        None,
    )


def check_for_new_items() -> Dict[int, List[LawData]]:
    logger.info(
        "Checking for new items from the list page API, paginating if necessary."
    )

    existing_ids: Set[str] = set()
    try:
        if not LAW_CATEGORIES:
            logger.warning(
                "No law categories defined (LAW_CATEGORIES is empty). Cannot check for existing items."
            )
            return {}

        valid_categories = []
        with closing(
            sqlite3.connect(
                f"file:{DB_PATH}?mode=ro",
                uri=True,
                timeout=10.0,
                check_same_thread=False,
            )
        ) as conn_check:
            cursor_check = conn_check.cursor()
            cursor_check.execute("SELECT name FROM sqlite_master WHERE type='table';")
            existing_tables = {row[0] for row in cursor_check.fetchall()}
            valid_categories = [cat for cat in LAW_CATEGORIES if cat in existing_tables]

        if not valid_categories:
            logger.warning(
                "No valid/existing law category tables found. Assuming no existing items."
            )
        else:
            union_query = " UNION ALL ".join(
                f'SELECT id FROM "{category}"' for category in valid_categories
            )

            with closing(
                sqlite3.connect(
                    f"file:{DB_PATH}?mode=ro",
                    uri=True,
                    timeout=10.0,
                    check_same_thread=False,
                )
            ) as conn:
                conn.execute("PRAGMA temp_store = MEMORY;")
                conn.execute("PRAGMA cache_size = -10000;")
                cursor = conn.cursor()
                logger.debug(
                    "Executing query to fetch all existing IDs from tables: %s",
                    valid_categories,
                )
                existing_ids = {row[0] for row in cursor.execute(union_query) if row[0]}
                logger.info(
                    "Found %d existing item IDs across %d table(s).",
                    len(existing_ids),
                    len(valid_categories),
                )

    except sqlite3.OperationalError as e:
        if "unable to open" in str(e):
            logger.warning(
                "Database file might not exist yet. Assuming no existing items: %s", e
            )
        else:
            logger.error(
                "Database operational error checking for existing items: %s",
                e,
                exc_info=True,
            )
            return {}
    except sqlite3.Error as e:
        logger.error(
            "General database error checking for existing items: %s", e, exc_info=True
        )
        return {}

    new_items_by_type: Dict[int, List[LawData]] = defaultdict(list)
    page: int = 1
    processed_count: int = 0
    skipped_existing_total: int = 0
    skipped_missing_type_total: int = 0

    list_base_url: str = LIST_PAGE_URL.split("&page=")[0]

    while True:
        logger.info("Fetching list page %d...", page)

        try:
            page_response = fetch_api_data(list_base_url, page)
            list_items: List[LawData] = page_response.get("result", {}).get("data", [])
            if "error" in page_response or not page_response.get("result"):
                logger.error(
                    "Failed to fetch or parse list page %d: %s. Stopping check.",
                    page,
                    page_response.get("error", "Unknown API error/structure"),
                )
                break

        except (ConnectionError, requests.exceptions.RequestException) as e:
            logger.error(
                "Failed to fetch list page %d: %s. Stopping check.",
                page,
                e,
                exc_info=True,
            )
            break

        if not list_items:
            logger.info("Reached end of list results at page %d.", page)
            break

        logger.debug("Processing %d items from page %d.", len(list_items), page)
        found_existing_on_page = False
        found_new_on_page = False

        for item in list_items:
            processed_count += 1
            item_id: Optional[str] = item.get("id")

            if not item_id:
                logger.debug(
                    "Skipping item without ID on page %d: title='%s'",
                    page,
                    item.get("title", "N/A"),
                )
                continue

            if item_id in existing_ids:
                skipped_existing_total += 1
                found_existing_on_page = True
                continue

            found_new_on_page = True
            api_type_identifier: Optional[str] = item.get("type")

            if not api_type_identifier:
                logger.warning(
                    "Skipping new item ID %s ('%s') on page %d: Missing 'type' identifier.",
                    item_id,
                    item.get("title", "N/A")[:50],
                    page,
                )
                skipped_missing_type_total += 1
                continue

            type_id: Optional[int] = get_type_id_from_code(api_type_identifier)

            if type_id is None:
                logger.warning(
                    "Skipping new item ID %s ('%s') on page %d: Cannot map API type '%s' to internal type ID.",
                    item_id,
                    item.get("title", "N/A")[:50],
                    page,
                    api_type_identifier,
                )
                skipped_missing_type_total += 1
                continue

            logger.info(
                "Found new item ID %s ('%s'), Type: %d on page %d",
                item_id,
                item.get("title", "N/A")[:50],
                type_id,
                page,
            )
            new_items_by_type[type_id].append(item)
            existing_ids.add(item_id)

        if found_existing_on_page and not found_new_on_page:
            logger.info(
                "Page %d contains only existing items. Stopping pagination check.", page
            )
            break

        page += 1

    result: Dict[int, List[LawData]] = dict(new_items_by_type)
    total_new = sum(len(items) for items in result.values())

    logger.info(
        "List page check complete. Total items processed (across pages): %d, Skipped (existing): %d, Skipped (no/invalid type): %d, New items found: %d across %d types.",
        processed_count,
        skipped_existing_total,
        skipped_missing_type_total,
        total_new,
        len(result),
    )

    if not result and processed_count > 0 and processed_count == skipped_existing_total:
        logger.info("All items found on checked page(s) already exist in the database.")
    elif not result and skipped_missing_type_total > 0:
        logger.warning(
            "No new items identified for processing, but %d item(s) were skipped due to missing/invalid type information.",
            skipped_missing_type_total,
        )

    return result


def process_new_items(
    download_enabled: bool, initial_delay: int, parse_enabled: bool = False
) -> None:
    logger.info("Processing new items identified from the list page check.")

    new_items_by_type: Dict[int, List[LawData]] = check_for_new_items()
    if not new_items_by_type:
        logger.info("No new items found to process.")
        return

    sql_template: str = (
        'INSERT OR IGNORE INTO "{}" (id, title, url, office, type, status, publish, expiry, saved, parsed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
    )

    db_pragmas: Tuple[str, ...] = (
        "PRAGMA journal_mode=WAL;",
        "PRAGMA synchronous=NORMAL;",
        "PRAGMA busy_timeout = 5000;",
        "PRAGMA cache_size=-10000;",
        "PRAGMA temp_store=MEMORY;",
    )

    max_workers = min(len(new_items_by_type), max(1, CPU_COUNT // 2), 2)
    logger.info(
        "Processing new items for %d types concurrently with %d workers.",
        len(new_items_by_type),
        max_workers,
    )

    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="ProcessNew"
    ) as executor:
        futures: Dict[Future[None], int] = {
            executor.submit(
                process_type_items,
                type_id,
                items,
                sql_template,
                db_pragmas,
                download_enabled,
                initial_delay,
                parse_enabled,
            ): type_id
            for type_id, items in new_items_by_type.items()
        }

        for future in chain(futures):
            type_id = futures[future]
            try:
                future.result()
            except Exception as e:
                logger.error(
                    f"Error processing new items for type {type_id} ({get_type_name(type_id)}): {e}",
                    exc_info=True,
                )

    logger.info("Finished processing new items from the list page.")


def process_type_items(
    type_id: int,
    items: List[LawData],
    sql_template: str,
    db_pragmas: Tuple[str, ...],
    download_enabled: bool,
    request_delay: float,
    parse_enabled: bool = False,
) -> None:
    type_code = get_type_code(type_id)
    type_name = get_type_name(type_id)
    if not type_code:
        logger.error("Invalid type_id %d in process_type_items.", type_id)
        return

    logger.info(
        "Processing %d new items for type %s (%s).", len(items), type_name, type_code
    )

    db_records: List[DbRow] = prepare_db_rows(items)
    if not db_records:
        logger.warning(
            "No valid database records prepared for new items of type %s. Aborting processing for this type.",
            type_name,
        )
        return

    sql = sql_template.format(type_code)
    inserted_ids: List[str] = []

    try:
        with closing(
            sqlite3.connect(
                DB_PATH, isolation_level=None, timeout=10.0, check_same_thread=False
            )
        ) as conn:
            conn.executescript(";".join(db_pragmas))

            with conn:
                cursor = conn.cursor()
                cursor.executemany(sql, db_records)
                changes = cursor.rowcount
                if changes > 0:
                    inserted_ids = [rec[0] for rec in db_records if rec[0]]

            logger.info(
                "Database operation complete for new %s items. Attempted: %d. Changes reported: %d.",
                type_code,
                len(db_records),
                changes,
            )

            if inserted_ids or download_enabled or parse_enabled:
                if download_enabled:
                    logger.info(
                        "Initiating document downloads for potentially %d new %s items.",
                        len(inserted_ids) if inserted_ids else len(db_records),
                        type_name,
                    )
                    download_all_docs(type_id, request_delay, auto_parse=parse_enabled)
                elif parse_enabled:
                    logger.info(
                        "Download disabled but parse enabled. Initiating parsing check for %s.",
                        type_name,
                    )
                    parse_saved_docs(type_id, request_delay)
            else:
                logger.info(
                    "No new items inserted and download/parse disabled for %s.",
                    type_name,
                )

    except sqlite3.Error as e:
        logger.error(
            "Database error saving new records for type %s (%s): %s",
            type_name,
            type_code,
            e,
            exc_info=True,
        )


def reorg_dfxfg_files() -> None:
    type_id: int = 6
    type_code: str = get_type_code(type_id)
    type_name: str = get_type_name(type_id)

    if not all((type_name, type_code)):
        logger.error(
            "Could not determine type name or code for ID %d. Cannot reorganize.",
            type_id,
        )
        return

    main_dir: Path = BASE_DIR / type_name
    if not (main_dir.exists() and main_dir.is_dir()):
        logger.warning(
            "Main directory for %s (%s) does not exist. Nothing to reorganize.",
            type_name,
            main_dir,
        )
        return

    logger.info(
        "Starting reorganization of files in '%s' directory (%s).", type_name, main_dir
    )

    file_mappings: Dict[str, str] = {}
    try:
        with closing(
            sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10.0)
        ) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query = f'SELECT title, office FROM "{type_code}" WHERE saved = 1 AND title IS NOT NULL AND office IS NOT NULL AND office != ""'
            cursor.execute(query)
            file_mappings = {
                row["title"]: row["office"]
                for row in cursor.fetchall()
                if row["title"] and row["office"]
            }

        if not file_mappings:
            logger.warning(
                "No saved entries with valid office information found in database table '%s'. Cannot reorganize files.",
                type_code,
            )
            return

        logger.info(
            "Found %d database entries with office information for type %s.",
            len(file_mappings),
            type_name,
        )
    except sqlite3.Error as e:
        logger.error(
            "Database error fetching office information for type %s: %s",
            type_name,
            e,
            exc_info=True,
        )
        return

    region_pattern: re.Pattern[str] = re.compile(r"^(.*?)人民代表大会")
    stats: Dict[str, int] = {"moved": 0, "skipped": 0, "error": 0}

    safe_title_map: Dict[str, Tuple[str, str]] = {
        re.sub(r'[/\\:*?"<>|]', "_", title).strip(): (title, office)
        for title, office in file_mappings.items()
    }
    simplified_title_map: Dict[str, Tuple[str, str]] = {
        "".join(
            c if c.isascii() and (c.isalnum() or c in (" ", "-", "_")) else "_"
            for c in title
        ).strip("_ "): (title, office)
        for title, office in file_mappings.items()
    }
    combined_map = {**simplified_title_map, **safe_title_map}

    for file_path in main_dir.glob("*.*"):
        if not file_path.is_file():
            continue

        file_name_stem: str = file_path.stem

        matched_entry: Optional[Tuple[str, str]] = combined_map.get(file_name_stem)

        if not matched_entry:
            match_candidate = next(
                (
                    (title, office)
                    for safe_title, (title, office) in combined_map.items()
                    if len(safe_title) >= 20
                    and file_name_stem.startswith(safe_title[:20])
                ),
                None,
            )
            if match_candidate:
                matched_entry = match_candidate
                logger.debug(
                    "Found potential match via prefix for file %s", file_path.name
                )

        if not matched_entry:
            logger.debug(
                "No matching database entry found for file stem '%s'. Skipping.",
                file_name_stem,
            )
            stats["skipped"] += 1
            continue

        original_title, office = matched_entry

        match = region_pattern.search(office)
        if match:
            region_name: Optional[str] = match.group(1).strip()
            if region_name:
                try:
                    sub_dir: Path = main_dir / region_name
                    sub_dir.mkdir(parents=True, exist_ok=True)

                    target_path: Path = sub_dir / file_path.name
                    if target_path.exists():
                        logger.warning(
                            "Target file %s already exists. Skipping move for %s.",
                            target_path,
                            file_path.name,
                        )
                        stats["skipped"] += 1
                    else:
                        file_path.rename(target_path)
                        logger.debug(
                            "Moved file '%s' to subdirectory '%s'.",
                            file_path.name,
                            region_name,
                        )
                        stats["moved"] += 1
                except Exception as e:
                    destination_dir_str: str = (
                        str(main_dir / region_name)
                        if region_name
                        else "destination directory"
                    )
                    logger.error(
                        "Error moving file %s to %s: %s",
                        file_path.name,
                        destination_dir_str,
                        e,
                    )
                    stats["error"] += 1
            else:
                logger.warning(
                    "Could not extract region name from office '%s' for file %s. Skipping.",
                    office,
                    file_path.name,
                )
                stats["skipped"] += 1
        else:
            logger.warning(
                "No region pattern match in office '%s' for file %s. Skipping.",
                office,
                file_path.name,
            )
            stats["skipped"] += 1

    logger.info(
        "Reorganization complete for %s. Moved: %d, Skipped: %d, Errors: %d.",
        type_name,
        stats["moved"],
        stats["skipped"],
        stats["error"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Chinese Law Database Crawler and Parser",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-t",
        "--type",
        type=int,
        default=0,
        help=f"Target law type ID (0 for all types). Available: {list(LAW_TYPES.keys())}",
    )
    parser.add_argument(
        "-c",
        "--check-list",
        action="store_true",
        help="Only check list page for new items and process them (downloads/parses if enabled).",
    )
    parser.add_argument(
        "-n",
        "--no-download",
        action="store_true",
        help="Disable downloading of documents (metadata will still be saved).",
    )
    parser.add_argument(
        "-r",
        "--reorganize",
        action="store_true",
        help="Only reorganize local files for type 6 (地方性法规) based on DB office info.",
    )
    parser.add_argument(
        "-p",
        "--parse-only",
        action="store_true",
        help="Only parse previously downloaded documents (saved=1, parsed=0).",
    )
    parser.add_argument(
        "-np",
        "--no-parse",
        action="store_true",
        help="Disable parsing of documents (both after download and in parse-only mode).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEF_REQ_DELAY,
        help="Base delay (seconds) between requests/downloads.",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=DEF_INIT_DELAY,
        help="Initial delay (seconds) for request retries (used in exponential backoff).",
    )

    args: argparse.Namespace = parser.parse_args()

    config: Dict[str, Any] = {
        "target_law_type": args.type,
        "enable_downloads": not args.no_download,
        "base_retry_delay": args.retry_delay,
        "inter_request_delay": args.delay,
        "check_list_page": args.check_list,
        "reorganize_files": args.reorganize,
        "parse_only": args.parse_only,
        "enable_parsing": not args.no_parse,
    }

    exit_code: int = 0
    try:
        with contextlib.ExitStack():
            start_time: float = time.monotonic()

            logger.info(
                "Script start. Config: LawType=%s, Downloads=%s, CheckList=%s, RequestDelay=%.2f, Reorganize=%s, ParseOnly=%s, EnableParsing=%s",
                "All" if config["target_law_type"] == 0 else config["target_law_type"],
                config["enable_downloads"],
                config["check_list_page"],
                config["inter_request_delay"],
                config["reorganize_files"],
                config["parse_only"],
                config["enable_parsing"],
            )

            initialize_database()

            if config["reorganize_files"]:
                logger.info("Mode: Reorganize files.")
                reorg_dfxfg_files()
            elif config["parse_only"]:
                logger.info("Mode: Parse-only.")
                if not config["enable_parsing"]:
                    logger.warning(
                        "Parse-only mode selected, but parsing is disabled via --no-parse. Exiting."
                    )
                else:
                    parse_saved_docs(
                        config["target_law_type"], config["inter_request_delay"]
                    )
            elif config["check_list_page"]:
                logger.info("Mode: Check list page for new items.")
                process_new_items(
                    download_enabled=config["enable_downloads"],
                    initial_delay=config["inter_request_delay"],
                    parse_enabled=config["enable_parsing"],
                )
            else:
                logger.info("Mode: Full crawl.")
                crawl_law_type(
                    type_id=config["target_law_type"],
                    download_enabled=config["enable_downloads"],
                    start_page=-1,
                    end_page=-1,
                    initial_delay=config["base_retry_delay"],
                    request_delay=config["inter_request_delay"],
                    parse_enabled=config["enable_parsing"],
                )

            logger.info(
                f"Script finished. Total execution time: {time.monotonic() - start_time:.2f} seconds"
            )

    except KeyboardInterrupt:
        logger.warning("Operation cancelled by user (KeyboardInterrupt).")
        print("\nOperation cancelled by user.", file=sys.stderr)
        exit_code = 130
    except SystemExit as e:
        logger.critical(
            f"Application exited via SystemExit (Code: {e.code}). Reason: %s",
            str(e),
            exc_info=False,
        )
        print(f"SystemExit: {e}", file=sys.stderr)
        exit_code = e.code if isinstance(e.code, int) else 1
    except Exception as e:
        logger.critical(f"An unexpected critical error occurred: {e}", exc_info=True)
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        exit_code = 1
    finally:
        logging.shutdown()
        sys.exit(exit_code)
