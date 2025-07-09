/*
A fork of <https://raw.githubusercontent.com/zZPiglet/Task/master/asset/flushDNS.js> with optimized features
*/

(() => {
  "use strict";

  const panelConfig = { title: "Flush DNS" };
  const settings = { showServer: true };

  const httpApi = (path, method = "POST", body = null) =>
    new Promise((resolve) => $httpAPI(method, path, body, resolve));

  (async () => {
    try {
      if (typeof $argument === "string") {
        for (const pair of $argument.split("&")) {
          const [key, value] = pair.split("=");
          if (!key) continue;
          switch (key) {
            case "title":
              panelConfig.title = value;
              break;
            case "icon":
              panelConfig.icon = value;
              break;
            case "color":
              panelConfig["icon-color"] = value;
              break;
            case "server":
              settings.showServer = value !== "false";
              break;
          }
        }
      }

      const requests = [httpApi("/v1/test/dns_delay")];
      if (settings.showServer) {
        requests.push(httpApi("/v1/dns", "GET"));
      }
      if ($trigger === "button") {
        httpApi("/v1/dns/flush").catch((err) =>
          console.log(`DNS flush error: ${err}`)
        );
      }

      const [dnsDelayResponse, dnsCache] = await Promise.all(requests);

      let serverListText = "";
      if (settings.showServer && dnsCache?.dnsCache) {
        const uniqueServers = [
          ...new Set(dnsCache.dnsCache.map((c) => c.server).filter(Boolean)),
        ];
        if (uniqueServers.length) {
          serverListText = `\nserver:\n${uniqueServers.join("\n")}`;
        }
      }

      panelConfig.content = `delay: ${Math.round(
        dnsDelayResponse.delay * 1000
      )}ms${serverListText}`;
    } catch (error) {
      panelConfig.content = `Error: ${error.message || error}`;
    } finally {
      $done(panelConfig);
    }
  })();
})();
