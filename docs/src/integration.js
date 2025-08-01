import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";

const processedFilesCache = new Map();
const dirCache = new Set();

// Error logging function that doesn't use console
function logError(/* message, error */) {
  // In a production environment, you might want to log to a file or service
  // For now, we'll just suppress the console errors
}

export function contentIntegration() {
  return {
    name: "content-integration",
    hooks: {
      "astro:build:start": async () => {
        try {
          await Promise.all([
            ensureDir("./public/laws"),
            ensureDir("./public/news"),
            ensureDir("./public/ruleset"),
            ensureDir("./public/assets"),
          ]);

          await Promise.all([
            processLaws(),
            processNews(),
            processRulesets(),
            generateContentIndex(),
          ]);
        } catch (error) {
          logError("Error processing content files:", error);
          throw error;
        }
      },
    },
  };
}

async function ensureDir(dir) {
  if (dirCache.has(dir)) return;

  try {
    await fs.mkdir(dir, { recursive: true });
    dirCache.add(dir);
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    dirCache.add(dir);
  }
}

async function processLaws() {
  const sourceDir = "../国家法律法规数据库";
  const targetDir = "./public/laws";

  const dirMap = {
    宪法: "constitution",
    法律: "statutes",
    行政法规: "administrative-regulations",
    地方性法规: "local-regulations",
    司法解释: "judicial-interpretations",
    监察法规: "supervisory-regulations",
  };

  const results = {
    totalProcessed: 0,
    categories: {},
  };

  const processingPromises = [];

  for (const [chineseName, englishPath] of Object.entries(dirMap)) {
    processingPromises.push(
      processLawCategory(
        sourceDir,
        targetDir,
        chineseName,
        englishPath,
        results
      )
    );
  }

  const readmePromise = fs
    .copyFile(
      path.join(sourceDir, "README.md"),
      path.join(targetDir, "README.md")
    )
    .catch(error => {
      if (error.code !== "ENOENT")
        logError("Error copying laws README:", error);
    });

  await Promise.all([...processingPromises, readmePromise]);

  await generateJsonIndex(path.join(targetDir, "index.json"), results);
}

async function processLawCategory(
  sourceDir,
  targetDir,
  chineseName,
  englishPath,
  results
) {
  const sourceCategoryDir = path.join(sourceDir, chineseName);
  const targetCategoryDir = path.join(targetDir, englishPath);

  await ensureDir(targetCategoryDir);

  try {
    await fs.access(sourceCategoryDir);
    const files = await fs.readdir(sourceCategoryDir);

    const mdFiles = files.filter(file => file.endsWith(".md"));
    results.totalProcessed += mdFiles.length;
    results.categories[englishPath] = mdFiles.length;

    const copyPromises = [];

    for (const file of mdFiles) {
      const cacheKey = path.join(sourceCategoryDir, file);

      if (processedFilesCache.has(cacheKey)) continue;

      const copyPromise = processMdFile(
        sourceCategoryDir,
        targetCategoryDir,
        file
      );
      copyPromises.push(copyPromise);

      copyPromise.then(() => processedFilesCache.set(cacheKey, true));
    }

    await Promise.all(copyPromises);
  } catch (error) {
    if (error.code !== "ENOENT")
      logError(`Error processing laws category ${chineseName}:`, error);
    results.categories[englishPath] = 0;
  }
}

async function processMdFile(sourceDir, targetDir, file) {
  const sourcePath = path.join(sourceDir, file);
  const targetPath = path.join(targetDir, file);

  try {
    const fileContent = await fs.readFile(sourcePath);
    const fileHash = createHash("md5")
      .update(fileContent)
      .digest("hex")
      .substring(0, 8);

    if (!fileContent.toString().includes("hash:")) {
      const updatedContent =
        fileContent.toString() + `\n\n<!-- hash: ${fileHash} -->\n`;
      await fs.writeFile(targetPath, updatedContent);
    } else {
      await fs.copyFile(sourcePath, targetPath);
    }
  } catch (e) {
    logError(`Failed to process ${file}:`, e);
  }
}

async function processNews() {
  const sourceDir = "../新闻联播";
  const targetDir = "./public/news";

  const years = [2022, 2023, 2024, 2025];
  const results = {
    totalProcessed: 0,
    years: {},
  };

  const yearPromises = years.map(year =>
    processNewsYear(sourceDir, targetDir, year, results)
  );

  const metaFilePromises = ["README.md", "catalogue.json"].map(file =>
    fs
      .copyFile(path.join(sourceDir, file), path.join(targetDir, file))
      .catch(error => {
        if (error.code !== "ENOENT")
          logError(`Error copying news file ${file}:`, error);
      })
  );

  await Promise.all([...yearPromises, ...metaFilePromises]);

  await generateJsonIndex(path.join(targetDir, "index.json"), results);
}

async function processNewsYear(sourceDir, targetDir, year, results) {
  const sourceYearDir = path.join(sourceDir, year.toString());
  const targetYearDir = path.join(targetDir, year.toString());

  try {
    await fs.access(sourceYearDir);
    await ensureDir(targetYearDir);

    const files = await fs.readdir(sourceYearDir);
    const mdFiles = files.filter(file => file.endsWith(".md"));

    results.totalProcessed += mdFiles.length;
    results.years[year.toString()] = mdFiles.length;

    const processPromises = [];

    for (const file of mdFiles) {
      const cacheKey = path.join(sourceYearDir, file);

      if (processedFilesCache.has(cacheKey)) continue;

      const processPromise = processNewsFile(
        sourceYearDir,
        targetYearDir,
        file
      );
      processPromises.push(processPromise);

      processPromise.then(() => processedFilesCache.set(cacheKey, true));
    }

    await Promise.all(processPromises);
  } catch (error) {
    if (error.code !== "ENOENT")
      logError(`Error processing news year ${year}:`, error);
    results.years[year.toString()] = 0;
  }
}

async function processNewsFile(sourceDir, targetDir, file) {
  const sourcePath = path.join(sourceDir, file);
  const targetPath = path.join(targetDir, file);

  try {
    const fileStats = await fs.stat(sourcePath);
    const fileContent = await fs.readFile(sourcePath, "utf-8");
    const updatedDate = new Date(fileStats.mtime).toISOString();

    if (!fileContent.includes("last_updated:")) {
      const updatedContent =
        fileContent + `\n\n<!-- last_updated: ${updatedDate} -->\n`;
      await fs.writeFile(targetPath, updatedContent);
    } else {
      await fs.copyFile(sourcePath, targetPath);
    }
  } catch (e) {
    logError(`Failed to process news file ${file}:`, e);
    try {
      await fs.copyFile(sourcePath, targetPath);
    } catch (err) {
      logError(`Fallback copy failed for ${file}:`, err);
    }
  }
}

async function processRulesets() {
  const sourceDir = "../Ruleset/dist";
  const targetDir = "./public/ruleset";

  try {
    await fs.access(sourceDir);
    await copyDirectory(sourceDir, targetDir);

    const stats = await fs.stat(sourceDir);
    const manifest = {
      lastUpdated: new Date(stats.mtime).toISOString(),
      description: "Ruleset distribution files",
      version: new Date().toISOString().slice(0, 10),
    };

    await fs.writeFile(
      path.join(targetDir, "manifest.json"),
      JSON.stringify(manifest, null, 2)
    );
  } catch (error) {
    if (error.code !== "ENOENT") logError("Error processing Ruleset:", error);
  }
}

async function copyDirectory(source, target) {
  await ensureDir(target);

  try {
    const entries = await fs.readdir(source, { withFileTypes: true });

    const directories = entries.filter(entry => entry.isDirectory());
    const files = entries.filter(entry => !entry.isDirectory());

    const copyFilePromises = files.map(async entry => {
      const sourcePath = path.join(source, entry.name);
      const targetPath = path.join(target, entry.name);

      const cacheKey = sourcePath;
      if (processedFilesCache.has(cacheKey)) return;

      try {
        await fs.copyFile(sourcePath, targetPath);
        processedFilesCache.set(cacheKey, true);
      } catch (e) {
        logError(`Failed to copy ${entry.name}:`, e);
      }
    });

    await Promise.all(copyFilePromises);

    await Promise.all(
      directories.map(dir =>
        copyDirectory(path.join(source, dir.name), path.join(target, dir.name))
      )
    );
  } catch (error) {
    logError(`Error copying directory ${source}:`, error);
  }
}

async function generateJsonIndex(filePath, data) {
  try {
    const indexData = {
      ...data,
      generated: new Date().toISOString(),
    };

    await fs.writeFile(filePath, JSON.stringify(indexData, null, 2));
  } catch (error) {
    logError(`Error generating index file ${filePath}:`, error);
  }
}

async function generateContentIndex() {
  try {
    const indexData = {
      collections: ["laws", "news", "ruleset"],
      generated: new Date().toISOString(),
      description: "Content index for Library",
    };

    await fs.writeFile(
      "./public/assets/content-index.json",
      JSON.stringify(indexData, null, 2)
    );

    const htmlIndex = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Library Content Index</title>
  <style>
    body{font-family:system-ui,-apple-system,sans-serif;max-width:1200px;margin:0 auto;padding:2rem}h1{color:#333}ul{list-style-type:none;padding:0}li{margin:.5rem 0}a{color:#06c;text-decoration:none}a:hover{text-decoration:underline}.collection{background:#f5f5f5;padding:1rem;border-radius:8px;margin-bottom:1rem}
  </style>
  <link rel="dns-prefetch" href="https://github.com">
  <link rel="preconnect" href="https://dearealeo.github.io">
</head>
<body>
  <h1>Library Content Index</h1>
  <div class="collection">
    <h2><a href="/Library/laws/">Laws Collection</a></h2>
    <ul>
      <li><a href="/Library/laws/constitution/">Constitution</a></li>
      <li><a href="/Library/laws/statutes/">Statutes</a></li>
      <li><a href="/Library/laws/administrative-regulations/">Administrative Regulations</a></li>
      <li><a href="/Library/laws/local-regulations/">Local Regulations</a></li>
      <li><a href="/Library/laws/judicial-interpretations/">Judicial Interpretations</a></li>
      <li><a href="/Library/laws/supervisory-regulations/">Supervisory Regulations</a></li>
    </ul>
  </div>
  <div class="collection">
    <h2><a href="/Library/news/">News Collection</a></h2>
    <ul>
      <li><a href="/Library/news/2022/">2022</a></li>
      <li><a href="/Library/news/2023/">2023</a></li>
      <li><a href="/Library/news/2024/">2024</a></li>
      <li><a href="/Library/news/2025/">2025</a></li>
    </ul>
  </div>
  <div class="collection">
    <h2><a href="/Library/ruleset/">Ruleset</a></h2>
  </div>
  <footer>
    <p>Generated on ${new Date().toLocaleString("zh-CN")}</p>
  </footer>
</body>
</html>`;

    await fs.writeFile("./public/assets/index.html", htmlIndex);
  } catch (error) {
    logError("Error generating content index:", error);
  }
}
