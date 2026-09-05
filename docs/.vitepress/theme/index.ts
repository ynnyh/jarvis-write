// 自定义主题:首页(index.md, layout: landing)渲染独立落地页,其余页面保持默认文档主题。
import DefaultTheme from 'vitepress/theme'
import { useData } from 'vitepress'
import { defineComponent, defineAsyncComponent, h } from 'vue'

import '@fontsource/noto-serif-sc/400.css'
import '@fontsource/noto-serif-sc/700.css'
import '@fontsource/noto-serif-sc/900.css'
import './global.css'

const Landing = defineAsyncComponent(() => import('./Landing.vue'))

const Layout = defineComponent({
  name: 'ThemeLayout',
  setup() {
    const { frontmatter } = useData()
    return () =>
      frontmatter.value.layout === 'landing' ? h(Landing) : h(DefaultTheme.Layout)
  },
})

export default {
  extends: DefaultTheme,
  Layout,
}
