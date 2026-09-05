// 站点级常量:官网落地页与文档导航共用的链接与在线体验配置。
// DEMO.url 留空时,落地页与文档导航自动隐藏「在线体验」入口。

export const REPO_URL = 'https://github.com/ynnyh/jarvis-write'
export const RELEASE_URL = 'https://github.com/ynnyh/jarvis-write/releases/latest'

// 在线体验(公开演示账号,试读内置样章)。三项填齐后,落地页与文档导航自动出现「在线试读」入口;
// 留空则隐藏并显示「筹备中」。demo 账号不配任何模型 API key,生成功能天然不可用,只可阅读。
export const DEMO = {
  url: 'http://111.228.10.230:8080/app/',
  account: 'demo',
  password: 'demo123456',
  note: '内置仙侠《破封纪》全本试读',
}

export const demoReady = Boolean(DEMO.url && DEMO.account && DEMO.password)
