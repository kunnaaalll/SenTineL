import nextConfig from "eslint-config-next";
import prettier from "eslint-config-prettier";

/** @type {import('eslint').Linter.Config[]} */
const eslintConfig = [
  {
    ignores: ["node_modules/**", ".next/**", "coverage/**", "next-env.d.ts", "*.config.js"],
  },
  ...nextConfig,
  {
    rules: {
      "@next/next/no-html-link-for-pages": "off",
    },
  },
  prettier,
];

export default eslintConfig;
