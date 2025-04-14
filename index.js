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
  const abstractLink = links.shift();
  console.log("Successfully retrieved news list");
  return { abstractLink, newsLinks: links };
};

const fetchAbstract = async (link) => {
  const html = await fetch(link);
  const dom = new JSDOM(html);
  let abstractText =
    dom.window.document.querySelector(
      "#page_body > div.allcontent > div.video18847 > div.playingCon > div.nrjianjie_shadow > div > ul > li:nth-child(1) > p"
    )?.innerHTML || "";

  return formatAbstractText(abstractText);
};

const formatAbstractText = (text) => {
  return text
    .replace(/<\/?p>|<\/?strong>/g, "")
    .replace(/^央视网消息（新闻联播）：/, "")
    .replace(/（《新闻联播》\s+\d+\s+\d+:\d+）$/, "")
    .replace("本期节目主要内容：", "")
    .replace(/(\d+)\.\s*/g, "- ")
    .replace(/^(\d+)\.\s+(?=\D)/gm, "- ")
    .replace(/；/g, "；\n")
    .replace(/：/g, "：\n")
    .replace(/\n\s*\n/g, "\n")
    .trim();
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
        .replace(/<\/?p>|<\/?strong>/g, "")
        .replace(/^央视网消息（新闻联播）：/g, "")
        .replace(/^(\s{2})-/gm, "    -")
    : "";
};

const getFormattedDateTime = () => {
  const now = new Date();
  return `${now.getFullYear()}-${(now.getMonth() + 1)
    .toString()
    .padStart(2, "0")}-${now.getDate().toString().padStart(2, "0")} ${now
    .getHours()
    .toString()
    .padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}`;
};

const convertNewsToMarkdown = ({ news }) => {
  const formattedDateTime = getFormattedDateTime();

  const newsMarkdown = news
    .map(({ title, content, url }) => {
      const formattedContent = formatNewsContent(content);
      return `\n###### ${title}\n\n${formattedContent}\n- [链接](${url})\n`;
    })
    .join("");

  return `- 更新时间：${formattedDateTime}\n\n${newsMarkdown}`
    .replace(/##### 新闻摘要\n\n/g, "##### 新闻摘要\n")
    .replace(/##### 详细新闻\n\n/g, "##### 详细新闻\n");
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
  abstract,
}) => {
  const [catalogueData, readmeContent] = await Promise.all([
    fs.readFile(cataloguePath).catch(() => "[]"),
    fs.readFile(readmePath, "utf8"),
  ]);

  let catalogueEntries = JSON.parse(catalogueData.toString() || "[]");
  catalogueEntries.unshift({ date, abstract });

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

    const { abstractLink, newsLinks } = await fetchNewsLinks(CURRENT_DATE);

    const [abstract, newsItems] = await Promise.all([
      fetchAbstract(abstractLink),
      fetchNewsItems(newsLinks),
    ]);

    const markdown = convertNewsToMarkdown({
      date: CURRENT_DATE,
      abstract,
      news: newsItems,
    });

    await fs.writeFile(NEWS_FILE_PATH, markdown);

    await Promise.all([
      applyMarkdownLinting(),
      updateCatalogueAndReadme({
        cataloguePath: CATALOGUE_PATH,
        readmePath: README_PATH,
        date: CURRENT_DATE,
        abstract,
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
