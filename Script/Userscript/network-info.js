/*
A fork of <https://raw.githubusercontent.com/Nebulosa-Cat/Surge/refs/heads/main/Panel/Network-Info/net-info-panel.js> with optimized features
*/

(() => {
  "use strict";
  const TIMEOUT = 5000;
  const RETRIES = 3;

  const getFlagEmoji = (countryCode) =>
    String.fromCodePoint(
      ...(countryCode ?? "")
        .toUpperCase()
        .split("")
        .map((c) => 127397 + c.charCodeAt())
    );

  const get = (options) =>
    new Promise((resolve, reject) => {
      $httpClient.get(options, (err, resp, data) =>
        err ? reject(err) : resolve({ ...resp, data })
      );
    });

  const getCellularInfo = () => {
    const cell = $network["cellular-data"];
    if (!cell?.radio) return "";
    const carrierMap = {
      // Taiwan
      "466-11": "中華電信",
      "466-92": "中華電信",
      "466-01": "遠傳電信",
      "466-03": "遠傳電信",
      "466-97": "台灣大哥大",
      "466-89": "台灣之星",
      "466-05": "GT",
      // China
      "460-03": "中国电信",
      "460-05": "中国电信",
      "460-11": "中国电信",
      "460-01": "中国联通",
      "460-06": "中国联通",
      "460-09": "中国联通",
      "460-00": "中国移动",
      "460-02": "中国移动",
      "460-04": "中国移动",
      "460-07": "中国移动",
      "460-08": "中国移动",
      "460-15": "中国广电",
      "460-20": "中移铁通",
      // HongKong
      "454-00": "CSL",
      "454-02": "CSL",
      "454-10": "CSL",
      "454-18": "CSL",
      "454-03": "3",
      "454-04": "3",
      "454-05": "3",
      "454-06": "SMC HK",
      "454-15": "SMC HK",
      "454-17": "SMC HK",
      "454-09": "CMHK",
      "454-12": "CMHK",
      "454-13": "CMHK",
      "454-28": "CMHK",
      "454-31": "CMHK",
      "454-16": "csl.",
      "454-19": "csl.",
      "454-20": "csl.",
      "454-29": "csl.",
      "454-01": "中信國際電訊",
      "454-07": "UNICOM HK",
      "454-08": "Truphone",
      "454-11": "CHKTL",
      "454-23": "Lycamobile",
      // Japan
      "440-00": "Y!mobile",
      "440-10": "docomo",
      "440-11": "Rakuten",
      "440-20": "SoftBank",
      "440-50": " au",
      "440-51": " au",
      "440-52": " au",
      "440-53": " au",
      "440-54": " au",
      "441-00": "WCP",
      "441-10": "UQ WiMAX",
      // Korea
      "450-03": "SKT",
      "450-05": "SKT",
      "450-02": "KT",
      "450-04": "KT",
      "450-08": "KT",
      "450-06": "LG U+",
      "450-10": "LG U+",
      // USA
      "310-030": "AT&T",
      "310-070": "AT&T",
      "310-150": "AT&T",
      "310-170": "AT&T",
      "310-280": "AT&T",
      "310-380": "AT&T",
      "310-410": "AT&T",
      "310-560": "AT&T",
      "310-680": "AT&T",
      "310-980": "AT&T",
      "310-160": "T-Mobile",
      "310-200": "T-Mobile",
      "310-210": "T-Mobile",
      "310-220": "T-Mobile",
      "310-230": "T-Mobile",
      "310-240": "T-Mobile",
      "310-250": "T-Mobile",
      "310-260": "T-Mobile",
      "310-270": "T-Mobile",
      "310-300": "T-Mobile",
      "310-310": "T-Mobile",
      "310-660": "T-Mobile",
      "310-800": "T-Mobile",
      "311-660": "T-Mobile",
      "311-882": "T-Mobile",
      "311-490": "T-Mobile",
      "312-530": "T-Mobile",
      "311-870": "T-Mobile",
      "311-880": "T-Mobile",
      "310-004": "Verizon",
      "310-010": "Verizon",
      "310-012": "Verizon",
      "310-013": "Verizon",
      "311-110": "Verizon",
      "311-270": "Verizon",
      "311-271": "Verizon",
      "311-272": "Verizon",
      "311-273": "Verizon",
      "311-274": "Verizon",
      "311-275": "Verizon",
      "311-276": "Verizon",
      "311-277": "Verizon",
      "311-278": "Verizon",
      "311-279": "Verizon",
      "311-280": "Verizon",
      "311-281": "Verizon",
      "311-282": "Verizon",
      "311-283": "Verizon",
      "311-284": "Verizon",
      "311-285": "Verizon",
      "311-286": "Verizon",
      "311-287": "Verizon",
      "311-288": "Verizon",
      "311-289": "Verizon",
      "311-390": "Verizon",
      "311-480": "Verizon",
      "311-481": "Verizon",
      "311-482": "Verizon",
      "311-483": "Verizon",
      "311-484": "Verizon",
      "311-485": "Verizon",
      "311-486": "Verizon",
      "311-487": "Verizon",
      "311-488": "Verizon",
      "311-489": "Verizon",
      "310-590": "Verizon",
      "310-890": "Verizon",
      "310-910": "Verizon",
      "310-120": "Sprint",
      "310-850": "Aeris Comm. Inc.",
      "310-510": "Airtel Wireless LLC",
      "312-090": "Allied Wireless Communications Corporation",
      "310-710": "Arctic Slope Telephone Association Cooperative Inc.",
      "311-440": "Bluegrass Wireless LLC",
      "311-800": "Bluegrass Wireless LLC",
      "311-810": "Bluegrass Wireless LLC",
      "310-900": "Cable & Communications Corp.",
      "311-590": "California RSA No. 3 Limited Partnership",
      "311-500": "Cambridge Telephone Company Inc.",
      "310-830": "Caprock Cellular Ltd.",
      "312-270": "Cellular Network Partnership LLC",
      "312-280": "Cellular Network Partnership LLC",
      "310-360": "Cellular Network Partnership LLC",
      "311-120": "Choice Phone LLC",
      "310-480": "Choice Phone LLC",
      "310-420": "Cincinnati Bell Wireless LLC",
      "310-180": "Cingular Wireless",
      "310-620": "Coleman County Telco /Trans TX",
      "310-06": "Consolidated Telcom",
      "310-60": "Consolidated Telcom",
      "310-700": "Cross Valliant Cellular Partnership",
      "312-030": "Cross Wireless Telephone Co.",
      "311-140": "Cross Wireless Telephone Co.",
      "312-040": "Custer Telephone Cooperative Inc.",
      "310-440": "Dobson Cellular Systems",
      "310-990": "E.N.M.R. Telephone Coop.",
      "312-120": "East Kentucky Network LLC",
      "312-130": "East Kentucky Network LLC",
      "310-750": "East Kentucky Network LLC",
      "310-090": "Edge Wireless LLC",
      "310-610": "Elkhart TelCo. / Epic Touch Co.",
      "311-311": "Farmers",
      "311-460": "Fisher Wireless Services Inc.",
      "311-370": "GCI Communication Corp.",
      "310-430": "GCI Communication Corp.",
      "310-920": "Get Mobile Inc.",
      "311-340": "Illinois Valley Cellular RSA 2 Partnership",
      "312-170": "Iowa RSA No. 2 Limited Partnership",
      "311-410": "Iowa RSA No. 2 Limited Partnership",
      "310-770": "Iowa Wireless Services LLC",
      "310-650": "Jasper",
      "310-870": "Kaplan Telephone Company Inc.",
      "312-180": "Keystone Wireless LLC",
      "310-690": "Keystone Wireless LLC",
      "311-310": "Lamar County Cellular",
      "310-016": "Leap Wireless International Inc.",
      "310-040": "Matanuska Tel. Assn. Inc.",
      "310-780": "Message Express Co. / Airlink PCS",
      "311-330": "Michigan Wireless LLC",
      "310-400": "Minnesota South. Wirel. Co. / Hickory",
      "311-010": "Missouri RSA No 5 Partnership",
      "312-010": "Missouri RSA No 5 Partnership",
      "311-020": "Missouri RSA No 5 Partnership",
      "312-220": "Missouri RSA No 5 Partnership",
      "311-920": "Missouri RSA No 5 Partnership",
      "310-350": "Mohave Cellular LP",
      "310-570": "MTPCS LLC",
      "310-290": "NEP Cellcorp Inc.",
      "310-34": "Nevada Wireless LLC",
      "310-600": "New-Cell Inc.",
      "311-300": "Nexus Communications Inc.",
      "310-130": "North Carolina RSA 3 Cellular Tel. Co.",
      "312-230": "North Dakota Network Company",
      "311-610": "North Dakota Network Company",
      "310-450": "Northeast Colorado Cellular Inc.",
      "311-710": "Northeast Wireless Networks LLC",
      "310-011": "Northstar",
      "310-670": "Northstar",
      "311-420": "Northwest Missouri Cellular Limited Partnership",
      "310-760": "Panhandle Telephone Cooperative Inc.",
      "310-580": "PCS ONE",
      "311-170": "PetroCom",
      "311-670": "Pine Belt Cellular, Inc.",
      "310-100": "Plateau Telecommunications Inc.",
      "310-940": "Poka Lambro Telco Ltd.",
      "310-500": "Public Service Cellular Inc.",
      "312-160": "RSA 1 Limited Partnership",
      "311-430": "RSA 1 Limited Partnership",
      "311-350": "Sagebrush Cellular Inc.",
      "310-46": "SIMMETRY",
      "311-260": "SLO Cellular Inc / Cellular One of San Luis",
      "310-320": "Smith Bagley Inc.",
      "316-011": "Southern Communications Services Inc.",
      "310-740": "Telemetrix Inc.",
      "310-14": "Testing",
      "310-860": "Texas RSA 15B2 Limited Partnership",
      "311-050": "Thumb Cellular Limited Partnership",
      "311-830": "Thumb Cellular Limited Partnership",
      "310-460": "TMP Corporation",
      "310-490": "Triton PCS",
      "312-290": "Uintah Basin Electronics Telecommunications Inc.",
      "311-860": "Uintah Basin Electronics Telecommunications Inc.",
      "310-960": "Uintah Basin Electronics Telecommunications Inc.",
      "310-020": "Union Telephone Co.",
      "311-220": "United States Cellular Corp.",
      "310-730": "United States Cellular Corp.",
      "311-650": "United Wireless Communications Inc.",
      "310-003": "Unknown",
      "310-15": "Unknown",
      "310-23": "Unknown",
      "310-24": "Unknown",
      "310-25": "Unknown",
      "310-26": "Unknown",
      "310-190": "Unknown",
      "310-950": "Unknown",
      "310-38": "USA 3650 AT&T",
      "310-999": "Various Networks",
      "310-520": "VeriSign",
      "310-530": "West Virginia Wireless",
      "310-340": "Westlink Communications, LLC",
      "311-070": "Wisconsin RSA #7 Limited Partnership",
      "310-390": "Yorkville Telephone Cooperative",
      // UK
      "234-08": "BT OnePhone UK",
      "234-02": "O2-UK",
      "234-10": "O2-UK",
      "234-11": "O2-UK",
      "234-15": "vodafone UK",
      "234-20": "3 UK",
      "234-30": "EE",
      "234-31": "EE",
      "234-32": "EE",
      "234-33": "EE",
      "234-34": "EE",
      "234-38": "Virgin",
      "234-50": "JT",
      "234-55": "Sure",
      "234-58": "Manx Telecom",
      // FR
      "208-01": "Orange",
      "208-02": "Orange",
      "208-15": "Free",
      "208-16": "Free",
      "208-20": "Bouygues",
      "208-88": "Bouygues",
      // Philippine
      "515-01": "Islacom",
      "515-02": "Globe",
      "515-03": "Smart",
      "515-04": "Sun",
      "515-08": "Next Mobile",
      "515-18": "Cure",
      "515-24": "ABS-CBN",
      // Vietnam
      "452-01": "Mobifone",
      "452-02": "VinaPhone",
      "452-03": "S-Fone",
      "452-04": "Viettel",
      "452-05": "VietNamobile",
      "452-06": "E-mobile",
      "452-07": "Gmobile",
      // Malaysia
      "502-10": "CelcomDigi",
      "502-13": "CelcomDigi",
      "502-19": "CelcomDigi",
      "502-150": "Tune Talk",
      "502-17": "Maxis",
    };
    const radioMap = {
      GPRS: "2.5G",
      EDGE: "2.75G",
      WCDMA: "3G",
      HSDPA: "3.5G",
      HSUPA: "3.75G",
      eHRPD: "3.9G",
      LTE: "4G",
      NRNSA: "5G",
      NR: "5G",
      CDMA1x: "2.5G",
      CDMAEVDORevA: "3.5G",
      CDMAEVDORev0: "3.5G",
      CDMAEVDORevB: "3.75G",
    };
    return `${carrierMap[cell.carrier] || "Cellular"} | ${
      radioMap[cell.radio] || cell.radio
    }`;
  };

  const getDeviceInfo = () => {
    const v4 = $network.v4?.primaryAddress;
    const v6 = $network.v6?.primaryAddress;
    if (!v4 && !v6) return "Network Disconnected\n";
    return (
      [
        v4 ? `Device IP: ${v4}` : null,
        v6 ? "IPv6 Address: Assigned" : null,
        $network.wifi?.ssid && $network.v4?.primaryRouter
          ? `Router IP: ${$network.v4.primaryRouter}`
          : null,
      ]
        .filter(Boolean)
        .join("\n") + "\n"
    );
  };

  const fetchNetworkInfo = async (attempt = 1) => {
    try {
      const resp = await get({
        url: "http://ip-api.com/json",
        timeout: TIMEOUT,
      });
      if (resp.status > 300) throw new Error(`HTTP Status ${resp.status}`);
      const info = JSON.parse(resp.data);
      return {
        title: $network.wifi?.ssid ?? getCellularInfo(),
        content: `${getDeviceInfo()}Node IP: ${info.query}\nNode ISP: ${
          info.isp
        }\nNode Location: ${getFlagEmoji(info.countryCode)} ${info.country} - ${
          info.city
        }`,
        icon: $network.wifi?.ssid ? "wifi" : "simcard",
        "icon-color": $network.wifi?.ssid ? "#5A9AF9" : "#8AB8DD",
      };
    } catch (err) {
      if (attempt < RETRIES) {
        await new Promise((r) => setTimeout(r, 1000));
        return fetchNetworkInfo(attempt + 1);
      }
      throw err;
    }
  };

  const scriptTimeout = setTimeout(() => {
    $done({
      title: "Request Timeout",
      content: "Network request timed out.\nPlease check your connection.",
      icon: "wifi.exclamationmark",
      "icon-color": "#CB1B45",
    });
  }, TIMEOUT * RETRIES + 2000);

  (async () => {
    let result;
    try {
      result = await fetchNetworkInfo();
    } catch (err) {
      result = {
        title: "Error",
        content: `Could not retrieve network info.\n${err.message || err}`,
        icon: "wifi.exclamationmark",
        "icon-color": "#CB1B45",
      };
    } finally {
      clearTimeout(scriptTimeout);
      $done(result);
    }
  })();
})();
