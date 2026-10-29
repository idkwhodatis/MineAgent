import { defineConfig } from "@hey-api/openapi-ts";
export default defineConfig({
  input: "../packages/contracts/research-input-audit.json",
  output: "src/input-audit-contracts",
  plugins: ["@hey-api/typescript"],
});
