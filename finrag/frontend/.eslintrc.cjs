/**
 * ESLint 配置（Vue 3 + Vite）。
 * 使用传统的 .eslintrc 格式，兼容 ESLint 8.x；执行 `npm run lint` 检查。
 */
module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
    node: true
  },
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module'
  },
  extends: ['eslint:recommended', 'plugin:vue/vue3-recommended'],
  globals: {
    // Vue 3 <script setup> 编译器宏
    defineProps: 'readonly',
    defineEmits: 'readonly',
    defineExpose: 'readonly',
    defineOptions: 'readonly',
    withDefaults: 'readonly'
  },
  rules: {
    // 视图/组件使用单词命名（ChatView、AppHeader 等），关闭多词限制
    'vue/multi-word-component-names': 'off',
    // 模板里通过 v-html 渲染 Markdown（回答正文），关闭该安全警告
    'vue/no-v-html': 'off',
    // 排版类规则交由编辑器/Prettier 处理，避免噪声
    'vue/max-attributes-per-line': 'off',
    'vue/singleline-html-element-content-newline': 'off',
    'vue/html-self-closing': 'off',
    'vue/attributes-order': 'off',
    'vue/html-indent': 'off',
    'vue/html-closing-bracket-newline': 'off',
    'vue/first-attribute-linebreak': 'off',
    'no-unused-vars': ['warn', { argsIgnorePattern: '^_', caughtErrors: 'none' }],
    'no-console': 'off',
    'no-empty': ['error', { allowEmptyCatch: true }]
  },
  ignorePatterns: ['dist', 'node_modules', '*.min.js']
}
