import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

/**
 * ESLint flat config.
 *
 * `eslint-config-next` ships its presets as flat-config arrays, so they are
 * spread directly rather than routed through the legacy `FlatCompat` shim.
 *
 * Accessibility is treated as a build gate, not advice: this is an Arabic-first
 * RTL interface for students, and `jsx-a11y` (bundled with the Next preset) is
 * the cheapest way to keep labels, roles and focus semantics honest.
 */
const config = [
  {
    ignores: ["node_modules/**", ".next/**", "next-env.d.ts", "coverage/**"],
  },
  ...coreWebVitals,
  ...typescript,
  {
    files: ["**/*.{ts,tsx}"],
    rules: {
      // A dead anchor or button is worse than no control at all.
      "jsx-a11y/anchor-is-valid": "error",
      "jsx-a11y/alt-text": "error",
      "jsx-a11y/aria-role": "error",
      "jsx-a11y/aria-props": "error",
      "jsx-a11y/role-has-required-aria-props": "error",
      // Extracted document text must never be injected as markup.
      "react/no-danger": "error",
      "react-hooks/exhaustive-deps": "error",
      "@typescript-eslint/no-explicit-any": "error",
      eqeqeq: ["error", "always", { null: "ignore" }],
    },
  },
  {
    // Tests legitimately stub the network boundary.
    files: ["**/__tests__/**/*.{ts,tsx}", "**/*.test.{ts,tsx}", "src/test/**"],
    rules: {
      "@typescript-eslint/no-non-null-assertion": "off",
      "@typescript-eslint/no-empty-object-type": "off",
    },
  },
];

export default config;
