#!/usr/bin/env python3
"""Test to verify the final naming convention with folders and underscores replaced."""

# Test examples: (original_name, category, expected_path)
test_cases = [
    ("stream_hk", "non_ip", "sing-box/json/non_ip/stream-hk.non_ip.json"),
    ("my_direct", "non_ip", "sing-box/json/non_ip/my-direct.non_ip.json"),
    ("icloud_private_relay", "domainset", "sing-box/json/domainset/icloud-private-relay.domainset.json"),
    ("apple-services", "non_ip", "sing-box/json/non_ip/apple-services.non_ip.json"),
    ("reject-no-drop", "non_ip", "sing-box/json/non_ip/reject-no-drop.non_ip.json"),
    ("telegram-asn", "ip", "sing-box/json/ip/telegram-asn.ip.json"),
    ("xiaomi", "local_dns", "sing-box/json/local_dns/xiaomi.local_dns.json"),
    ("lan_with_realip", "local_dns", "sing-box/json/local_dns/lan-with-realip.local_dns.json"),
]

print("Testing final naming convention:")
print("=" * 80)
for original_name, category, expected_path in test_cases:
    # Simulate the transformation
    base_name = original_name.replace("_", "-")
    result_path = f"sing-box/json/{category}/{base_name}.{category}.json"
    
    status = "✓" if result_path == expected_path else "✗"
    print(f"{status} {original_name} -> {result_path}")
    if result_path != expected_path:
        print(f"  Expected: {expected_path}")

print("\n" + "=" * 80)
print("SRS files (same structure, .json -> .srs):")
print("=" * 80)
for original_name, category, expected_path in test_cases[:3]:
    base_name = original_name.replace("_", "-")
    json_path = f"sing-box/json/{category}/{base_name}.{category}.json"
    srs_path = json_path.replace("json", "srs").replace(".json", ".srs")
    print(f"  {json_path}")
    print(f"  -> {srs_path}")
    print()
