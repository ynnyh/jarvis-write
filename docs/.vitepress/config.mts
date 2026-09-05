import { defineConfig } from 'vitepress'
import { DEMO, demoReady } from './site'

// jarvis-write 文档站配置。
// 站点直接以 docs/ 为源(与仓库内设计文档单一信息源),构建产物 docs/.vitepress/dist
// 由 GitHub Actions 部署到 GitHub Pages(项目站,故 base 必须带 /jarvis-write/ 前缀)。
export default defineConfig({
  title: 'jarvis-write',
  description: '可控、改得动、不崩的 AI 长篇小说创作系统',
  lang: 'zh-CN',
  // 项目 Pages 部署在 https://ynnyh.github.io/jarvis-write/,资源路径需带前缀。
  base: '/jarvis-write/',
  lastUpdated: true,
  cleanUrls: true,
  // 仓库里的设计文档即站点正文,无需排除;assets/ 下的图片可被 markdown 直接引用。
  srcExclude: ['**/node_modules/**'],

  // 站点头信息:页签图标、分享卡片、浏览器主题色。base 固定为 /jarvis-write/,head 里的
  // 站内路径需手写前缀(VitePress 不自动改写 head 里的 URL)。
  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/jarvis-write/favicon.svg' }],
    ['meta', { name: 'theme-color', content: '#f2f1ec' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:site_name', content: 'jarvis-write' }],
    ['meta', { property: 'og:title', content: 'jarvis-write · AI 长篇小说创作系统' }],
    ['meta', { property: 'og:description', content: '可控、改得动、不崩的 AI 长篇小说创作系统——时序故事圣经、伏笔调度、大纲级联、去 AI 味门禁。' }],
    ['meta', { property: 'og:url', content: 'https://ynnyh.github.io/jarvis-write/' }],
    ['meta', { property: 'og:image', content: 'https://ynnyh.github.io/jarvis-write/og-cover.png' }],
  ],

  themeConfig: {
    logo: undefined,
    siteTitle: 'jarvis-write',

    nav: [
      { text: '首页', link: '/' },
      { text: '功能导览', link: '/features' },
      { text: '设计文档', link: '/00-overview' },
      ...(demoReady
        ? [{ text: '在线试读', link: DEMO.url }]
        : []),
      {
        text: '获取',
        items: [
          { text: '下载桌面版(Windows)', link: 'https://github.com/ynnyh/jarvis-write/releases/latest' },
          { text: '全部 Release', link: 'https://github.com/ynnyh/jarvis-write/releases' },
        ],
      },
      { text: 'GitHub', link: 'https://github.com/ynnyh/jarvis-write' },
    ],

    sidebar: [
      { text: '功能导览', link: '/features' },
      {
        text: '设计文档',
        items: [
          { text: '00 · 项目愿景与调研对比', link: '/00-overview' },
          { text: '01 · 系统架构与技术选型', link: '/01-architecture' },
          { text: '02 · 数据模型设计', link: '/02-data-model' },
          { text: '03 · 三大引擎设计', link: '/03-engines' },
          { text: '04 · 标签化倾向系统', link: '/04-tag-system' },
          { text: '05 · 分阶段落地路线图', link: '/05-roadmap' },
        ],
      },
      {
        text: '交互与流程设计',
        items: [
          { text: '07 · 交互重构:双端设计与实施规格', link: '/07-交互重构-双端设计与实施规格' },
          { text: '08 · 章节生产流水线与前后审核体系', link: '/08-章节生产流水线与前后审核体系设计' },
          { text: '09 · 新建小说向导体验升级', link: '/09-新建小说向导体验升级设计' },
        ],
      },
      {
        text: '工程实践',
        items: [
          { text: '06 · 同步解耦与并发加固', link: '/06-改造方案-同步解耦与并发加固' },
          { text: '07 · 公网试用上线安全加固', link: '/07-公网试用上线-安全加固清单' },
        ],
      },
      {
        text: '更多',
        items: [
          { text: 'AI 供应商兼容性', link: '/ai-providers' },
          { text: '后端运行与测试', link: 'https://github.com/ynnyh/jarvis-write/blob/main/backend/README.md' },
          { text: '更新日志', link: 'https://github.com/ynnyh/jarvis-write/blob/main/CHANGELOG.md' },
        ],
      },
    ],

    // 内置本地搜索(纯前端,无需外部服务),文档多了之后尤其有用。
    search: { provider: 'local' },

    outline: { label: '本页目录', level: [2, 3] },
    docFooter: { prev: '上一篇', next: '下一篇' },
    lastUpdated: { text: '最后更新' },
    returnToTopLabel: '回到顶部',
    sidebarMenuLabel: '菜单',
    darkModeSwitchLabel: '主题',
    lightModeSwitchTitle: '切换到浅色',
    darkModeSwitchTitle: '切换到深色',

    footer: {
      message: '基于 Apache License 2.0 开源',
      copyright: 'Copyright © 2026 ynnyh',
    },
  },
})
