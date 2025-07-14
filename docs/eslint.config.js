import eslintPluginAstro from "eslint-plugin-astro";
import globals from "globals";
import tseslint from "typescript-eslint";

export default [
  ...tseslint.configs.recommended,
  ...eslintPluginAstro.configs.recommended,
  {
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
  },
  { rules: { "no-console": "error" } },
  {
    ignores: [
      "dist/**",
      ".astro/**",
      "public/pagefind/**",
      "Ruleset/**",
      "node_modules/**",
      "**/vendor/**",
      "**/news/**",
      "**/ruleset/**",
      "**/laws/**",
    ],
  },
  {
    files: [
      "**/Build/**",
      "**/build/**",
      "**/scripts/**",
      "**/test/**",
      "**/*.test.ts",
    ],
    rules: {
      "no-console": "off",
    },
  },
];
