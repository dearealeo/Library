/* jshint esversion: 11, module: true, node: true */
import fetch from "node-fetch";
import { setTimeout } from "timers/promises";

export default async function Fetch(url, options = {}) {
  const {
    retries = 3,
    retryDelay = 1000,
    timeout = 10000,
    ...fetchOptions
  } = options;

  const headers = {
    accept: "text/html, */*; q=0.01",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "cache-control": "no-cache",
    pragma: "no-cache",
    "sec-ch-ua":
      '"Microsoft Edge";v="107", "Chromium";v="107", "Not=A?Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "x-requested-with": "XMLHttpRequest",
    cookie:
      "cna=eY6BGb2h7yACAbSMsOm2vFG2; sca=5a4237a6; atpsida=6e052f524a88bc925aed09c0_1664038526_68",
    Referer: url,
    "Referrer-Policy": "strict-origin-when-cross-origin",
    ...fetchOptions.headers,
  };

  let lastError;
  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      /* global AbortController */
      const controller = new AbortController();
      const timeoutId = setTimeout(timeout, () => controller.abort());

      const response = await fetch(url, {
        method: "GET",
        headers,
        signal: controller.signal,
        ...fetchOptions,
        body: null,
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        throw new Error(
          `HTTP error ${response.status}: ${response.statusText}`
        );
      }

      return await response.text();
    } catch (err) {
      lastError = err;

      if (err.name === "AbortError") {
        throw new Error(`Request timed out after ${timeout}ms`);
      }

      if (attempt === retries - 1) {
        throw new Error(`Failed after ${retries} attempts: ${err.message}`);
      }

      const delay = retryDelay * Math.pow(2, attempt);
      console.warn(
        `Attempt ${attempt + 1} failed, retrying in ${delay}ms: ${err.message}`
      );
      await setTimeout(delay);
    }
  }

  throw lastError;
}
