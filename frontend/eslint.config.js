// ESLint 9 flat config:推荐规则 + react-hooks。
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  { ignores: ["dist"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // 项目代码风格里常用空 catch({}) / 空块占位,噪音大于收益
      "no-empty": ["error", { allowEmptyCatch: true }],
      // react-hooks v7 新规则:本项目所有面板都是"挂载/pid 变化 → 请求数据 → setState"
      // 的数据拉取模式,属于合法的 effect 用法,逐处改写会改变加载行为,故关闭
      "react-hooks/set-state-in-effect": "off",
    },
  },
);
