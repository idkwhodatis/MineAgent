import { defineConfig } from "@hey-api/openapi-ts";
export default defineConfig({
  input: "../packages/contracts/workflow-public.json",
  output: "src/workflow-contracts",
  plugins: ["@hey-api/typescript"],
});
