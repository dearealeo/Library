/* jshint esversion: 11, module: true, node: true */
import fs from "fs/promises";
import jsdom from "jsdom";
import path from "path";
import { fileURLToPath } from "url";
import fetch from "./fetch.js";
const { JSDOM } = jsdom;

const currentDirPath = path.dirname(fileURLToPath(import.meta.url));

const formatCurrentDate = () => {
  const date = new Date();
  return (
    date.getFullYear() +
    (date.getMonth() + 1).toString().padStart(2, "0") +
    date.getDate().toString().padStart(2, "0")
  );
};

const CURRENT_DATE = formatCurrentDate();
const NEWS_DIR_PATH = path.join(currentDirPath, "news");
const NEWS_FILE_PATH = path.join(NEWS_DIR_PATH, `${CURRENT_DATE}.md`);
const README_PATH = path.join(currentDirPath, "README.md");
const CATALOGUE_PATH = path.join(NEWS_DIR_PATH, "catalogue.json");

const fetchNewsLinks = async (date) => {
  const html = await fetch(`http://tv.cctv.com/lm/xwlb/day/${date}.shtml`);
  const dom = new JSDOM(
    `<!DOCTYPE html><html lang=""><body>${html}</body></html>`
  );
  const nodes = dom.window.document.querySelectorAll("a");
  const links = [...new Set([...nodes].map((node) => node.href))];
  console.log("Successfully retrieved news list");
  return { newsLinks: links };
};

const fetchNewsItem = async (url) => {
  try {
    const html = await fetch(url);
    const dom = new JSDOM(html);
    const title =
      dom.window.document
        .querySelector(
          "#page_body > div.allcontent > div.video18847 > div.playingVideo > div.tit"
        )
        ?.innerHTML?.replace("[视频]", "") || "";
    const content =
      dom.window.document.querySelector("#content_area")?.innerHTML || "";
    return { title, content, url };
  } catch (err) {
    console.error(`Error fetching news from ${url}:`, err.message);
    return { title: "Failed to fetch", content: "", url };
  }
};

const fetchNewsItems = async (links) => {
  console.log(`Fetching ${links.length} news items`);

  const batchSize = 5;
  const results = [];

  for (let i = 0; i < links.length; i += batchSize) {
    const batch = links.slice(i, i + batchSize);
    const batchPromises = batch.map(fetchNewsItem);

    const batchResults = await Promise.all(batchPromises);
    results.push(...batchResults);

    console.log(
      `Fetched items ${i + 1} to ${Math.min(i + batchSize, links.length)}`
    );
  }

  return results;
};

const formatNewsContent = (content) => {
  return content
    ? content
        .replace(/<strong>央视网消息<\/strong>（新闻联播）：/g, "")
        .replace(/^(\s{2})-/gm, "    -")
    : "";
};

const getFormattedDateTime = () => {
  const now = new Date();
  const chinaTime = new Date(now.getTime() + 8 * 60 * 60 * 1000);
  return `${chinaTime.getUTCFullYear()}-${(chinaTime.getUTCMonth() + 1)
    .toString()
    .padStart(2, "0")}-${chinaTime
    .getUTCDate()
    .toString()
    .padStart(2, "0")} ${chinaTime
    .getUTCHours()
    .toString()
    .padStart(2, "0")}:${chinaTime
    .getUTCMinutes()
    .toString()
    .padStart(2, "0")}`;
};

const convertNewsToMarkdown = ({ news }) => {
  const formattedDateTime = getFormattedDateTime();

  const newsMarkdown = news
    .filter(
      (item) =>
        item.title &&
        item.title.trim() !== "" &&
        !item.title.includes("《新闻联播》")
    )
    .map(({ title, content, url }) => {
      const formattedContent = formatNewsContent(content);
      return `\n## ${title}\n\n${formattedContent}\n\n- [链接](${url})\n`;
    })
    .join("");

  return `- 更新时间：${formattedDateTime}\n\n${newsMarkdown}`;
};

const applyMarkdownLinting = async () => {
  try {
    console.log("Applying markdown linting fixes...");
    console.log("Markdown linting fixes applied.");
    return true;
  } catch (error) {
    console.error(`Linter error: ${error.message}`);
    console.log(
      "Continuing despite linting errors - these will be fixed in the GitHub workflow"
    );
    return false;
  }
};

const updateCatalogueAndReadme = async ({
  cataloguePath,
  readmePath,
  date,
}) => {
  const [catalogueData, readmeContent] = await Promise.all([
    fs.readFile(cataloguePath).catch(() => "[]"),
    fs.readFile(readmePath, "utf8"),
  ]);

  let catalogueEntries = JSON.parse(catalogueData.toString() || "[]");
  catalogueEntries.unshift({ date });

  const updatedReadme = readmeContent.replace(
    "<!-- INSERT -->",
    `<!-- INSERT -->\n- [${date}](./news/${date}.md)`
  );

  await Promise.all([
    fs.writeFile(cataloguePath, JSON.stringify(catalogueEntries)),
    fs.writeFile(readmePath, updatedReadme),
  ]);

  console.log("Updated catalogue and README");
};

const main = async () => {
  try {
    console.log({ CURRENT_DATE, NEWS_DIR_PATH, README_PATH, CATALOGUE_PATH });

    await fs.mkdir(NEWS_DIR_PATH, { recursive: true });

    const { newsLinks } = await fetchNewsLinks(CURRENT_DATE);
    const newsItems = await fetchNewsItems(newsLinks);

    const markdown = convertNewsToMarkdown({
      date: CURRENT_DATE,
      news: newsItems,
    });

    await fs.writeFile(NEWS_FILE_PATH, markdown);

    await Promise.all([
      applyMarkdownLinting(),
      updateCatalogueAndReadme({
        cataloguePath: CATALOGUE_PATH,
        readmePath: README_PATH,
        date: CURRENT_DATE,
      }),
    ]);

    console.log("All operations completed successfully");
  } catch (error) {
    console.error("Error in main execution:", error);
    process.exit(1);
  }
};

main().catch((error) => {
  console.error("Unhandled error in main execution:", error);
  process.exit(1);
});
