/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}"],
  darkMode: "media",
  theme: {
    extend: {
      colors: {
        gray: {
          1: "rgb(142, 142, 147)",
          2: "rgb(174, 174, 178)",
          3: "rgb(199, 199, 204)",
          4: "rgb(209, 209, 214)",
          5: "rgb(229, 229, 234)",
          6: "rgb(242, 242, 247)",
        },
      },
    },
  },
  variants: {
    extend: {
      opacity: ["disabled"],
      cursor: ["disabled"],
    },
  },
  plugins: [],
  corePlugins: {
    preflight: false,
  },
};
