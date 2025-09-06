import { defineConfig } from "astro/config";
import { contentIntegration } from "./src/integration.js";
import sitemap from "@astrojs/sitemap";
import tailwindVite from "@tailwindcss/vite";
import mdx from "@astrojs/mdx";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

export default defineConfig({
  site: "https://dearealeo.github.io",
  base: "/Library",
  outDir: "./dist",
  publicDir: "./public",
  build: {
    assets: "_assets",
    format: "directory",
    inlineStylesheets: "never",
  },
  vite: {
    plugins: [tailwindVite()],
    build: {
      assetsInlineLimit: 0,
      cssCodeSplit: true,
      chunkSizeWarningLimit: 1000,
      reportCompressedSize: false,
      minify: "terser",
      terserOptions: {
        compress: {
          drop_console: true,
          drop_debugger: true,
          pure_funcs: ["console.log", "console.info", "console.debug"],
        },
      },
      rollupOptions: {
        output: {
          manualChunks: {
            markdown: [
              "marked",
              "remark-parse",
              "remark-rehype",
              "rehype-stringify",
            ],
            "core-vendors": ["react", "react-dom"],
          },
        },
      },
    },
    ssr: {
      noExternal: ["*"],
    },
    optimizeDeps: {
      exclude: ["@resvg/resvg-js"],
    },
  },
  markdown: {
    syntaxHighlight: "shiki",
    shikiConfig: {
      themes: { light: "github-light", dark: "github-dark" },
      wrap: true,
    },
    remarkPlugins: [remarkGfm, remarkMath],
    rehypePlugins: [rehypeKatex],
  },
  integrations: [
    contentIntegration(),
    sitemap({
      filter: page =>
        !page.includes("/laws/README") &&
        !page.includes("/news/README") &&
        !page.includes("/_") &&
        !page.includes(".md"),
    }),
    mdx({
      remarkPlugins: [remarkGfm, remarkMath],
      rehypePlugins: [rehypeKatex],
    }),
  ],
  output: "static",
  compressHTML: true,
  scopedStyleStrategy: "attribute",
});
