import asyncio  # noqa: CPY001, D100, EXE002, INP001
import ipaddress
from typing import Any

import anyio
import httpx
import orjson
import polars as pl
import yaml

cache: dict[str, list[str]] = {}
client = httpx.AsyncClient(
    timeout=httpx.Timeout(10.0, pool=30.0),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    http2=True,
)


mappings = {
    "DOMAIN": "domain",
    "DOMAIN-SUFFIX": "domain_suffix",
    "DOMAIN-KEYWORD": "domain_keyword",
    "DOMAIN-SET": "domain_suffix",
    "URL-REGEX": "domain_regex",
    "IP-CIDR": "ip_cidr",
    "IP-CIDR6": "ip_cidr",
    "SRC-IP": "source_ip_cidr",
    "SRC-IP-CIDR": "source_ip_cidr",
    "GEOIP": "geoip",
    "IP-ASN": "ip_asn",
    "DEST-PORT": "port",
    "DST-PORT": "port",
    "IN-PORT": "port",
    "SRC-PORT": "source_port",
    "PROCESS-NAME": "process_name",
    "PROTOCOL": "protocol",
    "HOST": "domain",
    "HOST-SUFFIX": "domain_suffix",
    "HOST-KEYWORD": "domain_keyword",
    "host": "domain",
    "host-keyword": "domain_keyword",
    "ip-cidr": "ip_cidr",
    "IP6-CIDR": "ip_cidr",
}

unsupported = frozenset({
    "USER-AGENT",
    "CELLULAR-RADIO",
    "DEVICE-NAME",
    "MAC-ADDRESS",
    "FINAL",
})


async def fetch(asn: str) -> list[str]:  # noqa: D103
    if asn in cache:
        return cache[asn]
    try:
        response = await client.get(f"https://api.bgpview.io/asn/{asn}/prefixes")
        if response.status_code != 200:  # noqa: PLR2004
            return []

        data = orjson.loads(response.content)
        if data.get("status") != "ok":
            return []

        prefixes = data.get("data", {})
        ipv4 = [p["prefix"] for p in prefixes.get("ipv4_prefixes", [])]
        ipv6 = [p["prefix"] for p in prefixes.get("ipv6_prefixes", [])]
        cidrs = ipv4 + ipv6

        cache[asn] = cidrs
        return cidrs

    except httpx.TimeoutException:
        return []
    else:
        cache[asn] = cidrs
        return cidrs


async def download(url: str) -> str:  # noqa: D103
    if url.startswith("file://"):
        file_path = url[7:]
        async with await anyio.Path(file_path).open("r", encoding="utf-8") as f:
            return await f.read()

    response = await client.get(url)
    response.raise_for_status()
    return response.text


def parse_yaml(content: str) -> list[dict[str, str]]:  # noqa: D103
    data = yaml.safe_load(content)
    rows = []

    for item in data.get("payload", []):
        address = item.strip("'")
        if "," not in item:
            if _is_network(address):
                pattern = "IP-CIDR"
            elif address.startswith("+"):
                pattern = "DOMAIN-SUFFIX"
                address = address[1:].lstrip(".")
            else:
                pattern = "DOMAIN"
        else:
            parts = item.split(",", 2)
            pattern = parts[0].strip()
            address = parts[1].strip()

        rows.append({"pattern": pattern, "address": address})

    return rows


def parse_list(content: str) -> list[dict[str, str]]:  # noqa: D103
    rows = []
    for raw_line in content.strip().split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(",", 2)
        if len(parts) >= 2:  # noqa: PLR2004
            rows.append({
                "pattern": parts[0].strip(),
                "address": parts[1].strip(),
            })
        elif len(parts) == 1:
            address = parts[0].strip()
            pattern = "DOMAIN-SUFFIX"
            address = address.removeprefix(".")
            rows.append({"pattern": pattern, "address": address})

    return rows


def _is_network(address: str) -> bool:
    try:
        ipaddress.ip_network(address, strict=False)
    except ValueError:
        return False
    else:
        return True


async def parse(url: str) -> pl.DataFrame:  # noqa: D103
    content = await download(url)

    if url.endswith((".yaml", ".yml")):
        try:
            rows = parse_yaml(content)
        except:  # noqa: E722
            rows = parse_list(content)
    else:
        rows = parse_list(content)

    return pl.DataFrame(rows)


async def convert(asns: list[str]) -> list[str]:  # noqa: D103
    tasks = [fetch(asn) for asn in asns]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    cidrs = []
    for result in results:
        if isinstance(result, list):
            cidrs.extend(result)

    return cidrs


def _build(dataframe: pl.DataFrame, cidrs: list[str]) -> dict[str, Any]:  # noqa: C901
    rules = {"version": 4, "rules": []}
    grouped = dataframe.group_by("pattern").agg(pl.col("address"))

    for row in grouped.iter_rows(named=True):
        pattern, addresses = row["pattern"], row["address"]

        if pattern == "domain_suffix":
            formatted = [f".{addr}" if not addr.startswith(".") else addr for addr in addresses]
            rules["rules"].append({pattern: formatted})
        elif pattern in {"domain", "domain_keyword", "domain_regex", "ip_cidr", "source_ip_cidr"}:
            rules["rules"].append({pattern: addresses})
        elif pattern in {"port", "source_port"}:
            try:
                ports = [int(p) for p in addresses]
                rules["rules"].append({pattern: ports})
            except ValueError:
                continue
        elif pattern == "process_name":
            rules["rules"].append({pattern: addresses})
        elif pattern == "protocol":
            protocols = [p.lower() for p in addresses]
            rules["rules"].append({"network": protocols})
        elif pattern == "geoip":
            rules["rules"].append({pattern: addresses})

    if cidrs:
        exists = False
        for rule in rules["rules"]:
            if "ip_cidr" in rule:
                rule["ip_cidr"].extend(cidrs)
                exists = True
                break

        if not exists:
            rules["rules"].append({"ip_cidr": cidrs})

    return rules


async def process(url: str, directory: str, category: str) -> anyio.Path | None:  # noqa: D103
    dataframe = await parse(url)

    if dataframe.height == 0 or len(dataframe.columns) == 0:
        return None

    dataframe = dataframe.filter(
        ~pl.col("pattern").str.contains("#")
        & ~pl.col("address").str.ends_with("-ruleset.skk.moe")
        & pl.col("pattern").is_in(list(mappings.keys())),
    )

    if dataframe.height == 0:
        return None

    filtered = dataframe.filter(pl.col("pattern").is_in(list(unsupported)))
    if filtered.height > 0:
        filtered["pattern"].unique().to_list()

    asns = dataframe.filter(pl.col("pattern") == "IP-ASN")
    cidrs = []
    if asns.height > 0:
        asn_list = asns["address"].unique().to_list()
        cidrs = await convert(asn_list)
        if cidrs:
            sum(1 for cidr in cidrs if ":" not in cidr)

    dataframe = dataframe.with_columns(pl.col("pattern").replace(mappings))

    await anyio.Path(directory).mkdir(exist_ok=True, parents=True)

    rules = _build(dataframe, cidrs)
    base_name = anyio.Path(url).stem.replace("_", "-")
    filename = anyio.Path(directory, f"{base_name}.{category}.json")

    async with await anyio.Path(filename).open("wb") as f:
        await f.write(orjson.dumps(rules, option=orjson.OPT_INDENT_2))

    return filename


async def main() -> None:  # noqa: C901, D103
    list_dir = anyio.Path("dist/List")

    if not await list_dir.exists():
        list_dir = anyio.Path("../dist/List")

    if not await list_dir.exists():
        return

    json_base = anyio.Path("sing-box/json")
    srs_base = anyio.Path("sing-box/srs")

    for base_dir in [json_base, srs_base]:
        for subdir in ["domainset", "ip", "non_ip", "local_dns"]:
            await (base_dir / subdir).mkdir(exist_ok=True, parents=True)

    conf_files = []
    for subdir in ["domainset", "ip", "non_ip"]:
        subdir_path = list_dir / subdir
        if await subdir_path.exists():
            conf_files.extend([(conf_file, subdir) async for conf_file in subdir_path.glob("*.conf")])

    results = []
    for conf_file, category in conf_files:
        file_url = f"file://{await conf_file.absolute()}"
        output_dir = json_base / category
        result = await process(file_url, str(output_dir), category)
        if result:
            results.append(result)

    modules_dir = anyio.Path("dist/Modules/Rules/sukka_local_dns_mapping")
    if not await modules_dir.exists():
        modules_dir = anyio.Path("../dist/Modules/Rules/sukka_local_dns_mapping")

    if await modules_dir.exists():
        async for conf_file in modules_dir.glob("*.conf"):
            file_url = f"file://{await conf_file.absolute()}"
            output_dir = json_base / "local_dns"
            result = await process(file_url, str(output_dir), "local_dns")
            if result:
                results.append(result)

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
