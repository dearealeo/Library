import asyncio  # noqa: CPY001, D100, INP001
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
    "DOMAIN-WILDCARD": "domain_wildcard",
    "IP-CIDR": "ip_cidr",
    "IP-CIDR6": "ip_cidr",
    "IP6-CIDR": "ip_cidr",
    "SRC-IP": "source_ip_cidr",
    "SRC-IP-CIDR": "source_ip_cidr",
    "IP-ASN": "ip_asn",
    "DEST-PORT": "port",
    "DST-PORT": "port",
    "IN-PORT": "port",
    "SRC-PORT": "source_port",
    "SOURCE-PORT": "source_port",
    "PROCESS-NAME": "process_name",
    "PROCESS-PATH": "process_path",
    "PROTOCOL": "network",
    "NETWORK": "network",
    "HOST": "domain",
    "HOST-SUFFIX": "domain_suffix",
    "HOST-KEYWORD": "domain_keyword",
    "host": "domain",
    "host-suffix": "domain_suffix",
    "host-keyword": "domain_keyword",
    "ip-cidr": "ip_cidr",
    "ip-cidr6": "ip_cidr",
}

RULE_ORDER = [
    "query_type",
    "network",
    "domain",
    "domain_suffix",
    "domain_keyword",
    "domain_regex",
    "source_ip_cidr",
    "ip_cidr",
    "source_port",
    "source_port_range",
    "port",
    "port_range",
    "process_name",
    "process_path",
    "process_path_regex",
    "package_name",
    "network_type",
    "network_is_expensive",
    "network_is_constrained",
    "network_interface_address",
    "default_interface_address",
    "wifi_ssid",
    "wifi_bssid",
    "invert",
]


unsupported = frozenset({
    "USER-AGENT",
    "CELLULAR-RADIO",
    "DEVICE-NAME",
    "MAC-ADDRESS",
    "FINAL",
    "GEOIP",
    "GEOSITE",
    "SOURCE-GEOIP",
})


async def fetch(asn: str) -> list[str]:  # noqa: D103
    if asn in cache:
        return cache[asn]

    asn_number = asn.replace("AS", "").replace("as", "")
    cidrs = []

    try:
        response = await client.get(f"https://api.bgpview.io/asn/{asn_number}/prefixes")
        if response.status_code == 200:  # noqa: PLR2004
            data = orjson.loads(response.content)
            if data.get("status") == "ok":
                prefixes = data.get("data", {})
                ipv4 = [p["prefix"] for p in prefixes.get("ipv4_prefixes", [])]
                ipv6 = [p["prefix"] for p in prefixes.get("ipv6_prefixes", [])]
                cidrs = ipv4 + ipv6
                if cidrs:
                    cache[asn] = cidrs
                    return cidrs
    except (httpx.HTTPError, orjson.JSONDecodeError, KeyError):
        pass
    try:
        response = await client.get(
            f"https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS{asn_number}",
        )
        if response.status_code == 200:  # noqa: PLR2004
            data = orjson.loads(response.content)
            if data.get("status") == "ok":
                prefixes = data.get("data", {}).get("prefixes", [])
                cidrs = [p["prefix"] for p in prefixes if "prefix" in p]
                if cidrs:
                    cache[asn] = cidrs
                    return cidrs
    except (httpx.HTTPError, orjson.JSONDecodeError, KeyError):
        pass
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
        address = item.strip("'\"")
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

            if len(parts) >= 3:  # noqa: PLR2004
                pass

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
            pattern = parts[0].strip()
            address = parts[1].strip()

            if len(parts) >= 3 and parts[2].strip().lower() == "no-resolve":  # noqa: PLR2004
                pass

            rows.append({
                "pattern": pattern,
                "address": address,
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


def _wildcard_to_regex(wildcard: str) -> str:
    wildcard = wildcard.lstrip(".")
    escaped = wildcard.replace(".", r"\.").replace("*", "WILDCARD_PLACEHOLDER")
    regex = escaped.replace("WILDCARD_PLACEHOLDER", r"[^.]+")
    return f"^{regex}$"


def _normalize_ip_cidr(ip_cidr: str) -> str:
    if "/" in ip_cidr:
        return ip_cidr
    try:
        addr = ipaddress.ip_address(ip_cidr)
        if addr.version == 4:  # noqa: PLR2004
            return f"{ip_cidr}/32"
    except ValueError:
        return ip_cidr
    else:
        return f"{ip_cidr}/128"


def _parse_port_range(port_str: str) -> tuple[str | None, int | None]:
    if ":" in port_str or "-" in port_str:
        separator = ":" if ":" in port_str else "-"
        parts = port_str.split(separator)
        if len(parts) == 2:  # noqa: PLR2004
            try:
                start, end = int(parts[0]), int(parts[1])
            except ValueError:
                return None, None
            else:
                return f"{start}:{end}", None
    else:
        try:
            return None, int(port_str)
        except ValueError:
            return None, None
    return None


def _build(dataframe: pl.DataFrame, cidrs: list[str]) -> dict[str, Any]:  # noqa: C901, PLR0912, PLR0914, PLR0915
    rules = {"version": 4, "rules": [{}]}
    rule_dict = rules["rules"][0]

    grouped = dataframe.group_by("pattern").agg(pl.col("address"))

    for row in grouped.iter_rows(named=True):
        pattern, addresses = row["pattern"], row["address"]

        if pattern == "domain":
            rule_dict.setdefault("domain", []).extend(addresses)

        elif pattern == "domain_suffix":
            formatted = [f".{addr}" if not addr.startswith(".") else addr for addr in addresses]
            rule_dict.setdefault("domain_suffix", []).extend(formatted)

        elif pattern == "domain_keyword":
            rule_dict.setdefault("domain_keyword", []).extend(addresses)

        elif pattern == "domain_regex":
            rule_dict.setdefault("domain_regex", []).extend(addresses)

        elif pattern == "domain_wildcard":
            regexes = [_wildcard_to_regex(addr) for addr in addresses]
            rule_dict.setdefault("domain_regex", []).extend(regexes)

        elif pattern == "ip_cidr":
            normalized = [_normalize_ip_cidr(addr) for addr in addresses]
            rule_dict.setdefault("ip_cidr", []).extend(normalized)

        elif pattern == "source_ip_cidr":
            normalized = [_normalize_ip_cidr(addr) for addr in addresses]
            rule_dict.setdefault("source_ip_cidr", []).extend(normalized)

        elif pattern == "port":
            ports = []
            port_ranges = []
            for addr in addresses:
                range_str, single_port = _parse_port_range(addr)
                if range_str:
                    port_ranges.append(range_str)
                elif single_port is not None:
                    ports.append(single_port)

            if ports:
                rule_dict.setdefault("port", []).extend(ports)
            if port_ranges:
                rule_dict.setdefault("port_range", []).extend(port_ranges)

        elif pattern == "source_port":
            ports = []
            port_ranges = []
            for addr in addresses:
                range_str, single_port = _parse_port_range(addr)
                if range_str:
                    port_ranges.append(range_str)
                elif single_port is not None:
                    ports.append(single_port)

            if ports:
                rule_dict.setdefault("source_port", []).extend(ports)
            if port_ranges:
                rule_dict.setdefault("source_port_range", []).extend(port_ranges)

        elif pattern == "process_name":
            rule_dict.setdefault("process_name", []).extend(addresses)

        elif pattern == "process_path":
            rule_dict.setdefault("process_path", []).extend(addresses)

        elif pattern == "network":
            protocols = []
            for p in addresses:
                p_upper = p.upper()
                if p_upper in {"TCP", "UDP", "ICMP"}:
                    protocols.append(p.lower())
            if protocols:
                rule_dict.setdefault("network", []).extend(protocols)

    if cidrs:
        normalized_cidrs = [_normalize_ip_cidr(cidr) for cidr in cidrs]
        rule_dict.setdefault("ip_cidr", []).extend(normalized_cidrs)

    for key in rule_dict:
        if isinstance(rule_dict[key], list):
            if key in {"port", "source_port"}:
                rule_dict[key] = sorted(set(rule_dict[key]))
            elif key in {"port_range", "source_port_range"}:
                seen = set()
                unique_list = []
                for item in rule_dict[key]:
                    if item not in seen:
                        seen.add(item)
                        unique_list.append(item)
                rule_dict[key] = unique_list
            else:
                seen = set()
                unique_list = []
                for item in rule_dict[key]:
                    if item not in seen:
                        seen.add(item)
                        unique_list.append(item)
                rule_dict[key] = unique_list

    ordered_rule = {}
    for field in RULE_ORDER:
        if rule_dict.get(field):
            ordered_rule[field] = rule_dict[field]

    for field in rule_dict:
        if field not in ordered_rule and rule_dict[field]:
            ordered_rule[field] = rule_dict[field]

    if not ordered_rule:
        return {"version": 2, "rules": []}

    rules["rules"][0] = ordered_rule

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
        unsupported_patterns = filtered["pattern"].unique().to_list()

        deprecated_geo = [p for p in unsupported_patterns if p in {"GEOIP", "GEOSITE", "SOURCE-GEOIP"}]
        [p for p in unsupported_patterns if p not in deprecated_geo]
    asns = dataframe.filter(pl.col("pattern") == "IP-ASN")
    cidrs = []
    if asns.height > 0:
        asn_list = asns["address"].unique().to_list()
        cidrs = await convert(asn_list)
    dataframe = dataframe.with_columns(pl.col("pattern").replace(mappings))

    await anyio.Path(directory).mkdir(exist_ok=True, parents=True)

    rules = _build(dataframe, cidrs)
    base_name = anyio.Path(url).stem.replace("_", "-")
    filename = anyio.Path(directory, f"{base_name}.{category}.json")

    rule_stats = {}
    if rules["rules"] and len(rules["rules"]) > 0:
        for key, value in rules["rules"][0].items():
            if isinstance(value, list):
                rule_stats[key] = len(value)

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
        local_dns_files = [f async for f in modules_dir.glob("*.conf")]
        for conf_file in local_dns_files:
            file_url = f"file://{await conf_file.absolute()}"
            output_dir = json_base / "local_dns"
            result = await process(file_url, str(output_dir), "local_dns")
            if result:
                results.append(result)

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
