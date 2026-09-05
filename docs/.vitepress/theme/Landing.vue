<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { withBase } from 'vitepress'
import { DEMO, RELEASE_URL, REPO_URL, demoReady } from '../site'
import FlavorDemo from './components/FlavorDemo.vue'
import BrandMark from './components/BrandMark.vue'
import shotWorkbench from '../../assets/screenshots/01-workbench.png'
import shotWorkshop from '../../assets/screenshots/04-story-workshop.png'
import shotShelf from '../../assets/screenshots/02-home.png'
import qqQr from '../../assets/qq-group-qr.jpg'

const scrolled = ref(false)

function onScroll() {
  scrolled.value = window.scrollY > 24
}

let observer: IntersectionObserver | null = null

onMounted(() => {
  onScroll()
  window.addEventListener('scroll', onScroll, { passive: true })
  observer = new IntersectionObserver(
    (entries) => {
      for (const el of entries) {
        if (el.isIntersecting) {
          el.target.classList.add('in')
          observer?.unobserve(el.target)
        }
      }
    },
    { threshold: 0.12 },
  )
  document.querySelectorAll('[data-reveal]').forEach((el) => observer?.observe(el))
})

onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
  observer?.disconnect()
})

const navLinks = [
  { text: '理念', href: '#ideas' },
  { text: '去 AI 味', href: '#flavor' },
  { text: '核心引擎', href: '#engines' },
  { text: '功能导览', href: withBase('/features') },
  { text: '设计文档', href: withBase('/00-overview') },
]

const capabilities = [
  {
    seal: '壹',
    title: '时序故事圣经',
    desc: '每条事实绑定生效章节区间,可查「第 N 章时他是什么状态」;章后自动抽取实体与事实写回圣经,几十万字不自相矛盾。',
  },
  {
    seal: '贰',
    title: '伏笔四态调度',
    desc: '埋设、强化、回收、弃用,四态全生命周期管理,到期自动提醒——埋了的线,不会写丢。',
  },
  {
    seal: '叁',
    title: '大纲级联更新',
    desc: '改任意一章大纲:改动分级、下游影响分析、勾选后级联重生成;已有正文自动标记失配,大纲全程版本化可回退。',
  },
  {
    seal: '肆',
    title: '去 AI 味门禁',
    desc: '九类套话规则 + 句长节奏统计 + 叙事架构指纹(模板转折/点题说教/结局三脚架),定稿前量化把关;超标定向重写、复测收敛,校准纪律防矫枉过正。',
  },
  {
    seal: '伍',
    title: '标签化倾向系统',
    desc: '风格、节奏、基调不写死在 Prompt 里:chips + 自定义 + 预设模板,贯穿大纲、正文、润色三个节点,全程你说了算。',
  },
  {
    seal: '陆',
    title: '漫剧与短片工坊',
    desc: '定稿章节一键改编竖屏漫剧;宣传片、情绪短片、生日祝福各有工坊——分镜、三轨绘图提示词、配音稿、成片包,拿去即梦/可灵/剪映直接出片。',
  },
]

const ideas = [
  {
    no: '01',
    title: '可控',
    desc: '生成文字交给 LLM,控制层留在本地:事实校对、一致性检查、AI 味门禁,不合格的章节进不了定稿。',
  },
  {
    no: '02',
    title: '可改',
    desc: '改一章大纲,系统分析下游影响,你勾选后级联重生成;AI 改完不覆盖,逐条 diff 验收,一条常驻 AI 客栏随叫随到。',
  },
  {
    no: '03',
    title: '可追溯',
    desc: '每章版本留档,润色和重写失败自动回退,绝不落更差的版本;写了什么、改了什么、为什么改,全都查得到。',
  },
]
</script>

<template>
  <div class="lw" :class="{ scrolled }">
    <!-- 顶部导航 -->
    <header class="lw-nav">
      <div class="lw-shell nav-inner">
        <a class="brand" :href="withBase('/')">
          <BrandMark :size="34" class="brand-mark" />
          <span class="brand-text">
            <b>jarvis-write</b>
            <i>AI 长篇小说工作台</i>
          </span>
        </a>
        <nav class="nav-links" aria-label="站内导航">
          <a v-for="l in navLinks" :key="l.text" :href="l.href">{{ l.text }}</a>
        </nav>
        <div class="nav-cta">
          <a v-if="demoReady" class="btn line sm" :href="DEMO.url" target="_blank" rel="noopener">在线试读</a>
          <a class="btn solid sm" :href="RELEASE_URL" target="_blank" rel="noopener">下载桌面版</a>
        </div>
      </div>
    </header>

    <!-- 首屏 -->
    <section class="hero">
      <div class="lw-shell hero-inner">
        <div class="hero-copy" data-reveal>
          <p class="kicker">本地优先 · 开源 · 可自部署</p>
          <h1 class="hero-title">
            草蛇灰线,<br />
            <em>伏脉千里</em>。
          </h1>
          <p class="hero-sub">
            jarvis-write 是包在大模型外面的控制层:时序故事圣经管事实,伏笔调度管回收,
            大纲级联管改动,去 AI 味门禁管文风——模型只管写字,<b>章法归你</b>。
          </p>
          <div class="cta-row">
            <a class="btn solid lg" :href="RELEASE_URL" target="_blank" rel="noopener">下载 Windows 桌面版</a>
            <a v-if="demoReady" class="btn line lg" :href="DEMO.url" target="_blank" rel="noopener">在线试读样章 →</a>
            <a class="btn quiet lg" :href="REPO_URL" target="_blank" rel="noopener">GitHub</a>
          </div>
          <p class="hero-meta">Apache-2.0 开源 · 900+ 项自动化测试 · 桌面版单机免登录 / Docker 多用户自部署</p>
        </div>
        <div class="hero-visual" data-reveal>
          <figure class="shot-frame">
            <figcaption class="shot-bar" aria-hidden="true">
              <i></i><i></i><i></i>
              <span>写作 · 《霓虹深渊》第 13 章</span>
            </figcaption>
            <img :src="shotWorkbench" alt="jarvis-write 写作工作台:正文、章节蓝图与 AI 客栏" loading="eager" />
          </figure>
          <div class="float-card fc-a" aria-hidden="true">
            <p class="fc-tag">伏笔调度</p>
            <p class="fc-body">「芯片里的名字」第 19 章到期 · <b>待回收</b></p>
          </div>
          <div class="float-card fc-b" aria-hidden="true">
            <p class="fc-tag">故事圣经</p>
            <p class="fc-body">凯恩 · 义体排异症 <b>绑定第 2–13 章生效</b></p>
          </div>
        </div>
      </div>
    </section>

    <!-- 三理念 -->
    <section id="ideas" class="band">
      <div class="lw-shell">
        <p class="kicker accent" data-reveal>为什么是它</p>
        <h2 class="band-title" data-reveal>长篇创作,三件事不能让。</h2>
        <div class="ideas-grid">
          <article v-for="(it, i) in ideas" :key="it.no" class="idea" data-reveal :style="{ transitionDelay: `${i * 70}ms` }">
            <span class="idea-no">{{ it.no }}</span>
            <h3>{{ it.title }}</h3>
            <p>{{ it.desc }}</p>
          </article>
        </div>
      </div>
    </section>

    <!-- 去 AI 味演示(牛皮纸稿面) -->
    <section id="flavor" class="band flavor-band">
      <div class="lw-shell">
        <p class="kicker accent" data-reveal>编辑部的偏执</p>
        <h2 class="band-title" data-reveal>去 AI 味,是门禁,不是建议。</h2>
        <p class="band-sub" data-reveal>
          下面每一组对照都来自 jarvis-write 定稿门禁的内置规则:左边是模型最爱写的套话,右边是门禁要求改成的样子。
        </p>
        <div data-reveal>
          <FlavorDemo />
        </div>
        <p class="band-note" data-reveal>
          还可以正向喂文风:余华、鲁迅、汪曾祺、金庸、王小波、海明威任选,也可喂自己的范文——均标注「风格参考 · 非原作节选」。
        </p>
      </div>
    </section>

    <!-- 核心引擎 -->
    <section id="engines" class="band">
      <div class="lw-shell">
        <p class="kicker accent" data-reveal>核心能力</p>
        <h2 class="band-title" data-reveal>让长篇全程可控的六个引擎。</h2>
        <div class="engines-grid">
          <article v-for="(it, i) in capabilities" :key="it.seal" class="engine" data-reveal :style="{ transitionDelay: `${(i % 3) * 70}ms` }">
            <span class="engine-seal" aria-hidden="true">{{ it.seal }}</span>
            <h3>{{ it.title }}</h3>
            <p>{{ it.desc }}</p>
          </article>
        </div>
      </div>
    </section>

    <!-- 界面速览 -->
    <section class="band shots">
      <div class="lw-shell">
        <div class="shots-grid">
          <figure class="shot-card" data-reveal>
            <img :src="shotWorkshop" alt="故事工坊:一段话点子,一次产三个本子" loading="lazy" />
            <figcaption><b>故事工坊</b>一段话的点子,三十秒出三个不同切入的本子,画风任选,先锁脸再逐段出片。</figcaption>
          </figure>
          <figure class="shot-card" data-reveal>
            <img :src="shotShelf" alt="书架:多本并行管理" loading="lazy" />
            <figcaption><b>书架</b>多本并行,进度、状态、字数守卫一目了然;整本可导出 txt / epub。</figcaption>
          </figure>
        </div>
      </div>
    </section>

    <!-- 下载 + 收尾 -->
    <section id="download" class="band final">
      <div class="lw-shell">
        <p class="kicker gold" data-reveal>现在开始</p>
        <h2 class="band-title light" data-reveal>埋下去的每条线,<br />都有人记得收。</h2>
        <p class="band-sub" data-reveal>伏笔不丢,人设不崩,文风像人——从第一章守到最后一章。</p>
        <div class="dl-grid">
          <a class="dl-card" :href="RELEASE_URL" target="_blank" rel="noopener" data-reveal>
            <h3>Windows 桌面版</h3>
            <p>安装包免登录单机运行,数据落本机;新版本自动提醒,一键升级。</p>
            <span class="dl-go">获取最新版 →</span>
          </a>
          <a class="dl-card" href="https://github.com/ynnyh/jarvis-write#readme" target="_blank" rel="noopener" data-reveal>
            <h3>Docker 自部署</h3>
            <p>多用户服务:JWT 登录、邀请码、数据隔离,一条 compose 起自己的创作站。</p>
            <span class="dl-go">部署文档 →</span>
          </a>
          <div v-if="demoReady" class="dl-card demo" data-reveal>
            <h3>在线试读</h3>
            <p>{{ DEMO.note }}。无需安装,打开即读:</p>
            <p class="dl-cred">账号 <code>{{ DEMO.account }}</code> · 密码 <code>{{ DEMO.password }}</code></p>
            <a class="dl-go" :href="DEMO.url" target="_blank" rel="noopener">打开体验站 →</a>
          </div>
          <div v-else class="dl-card demo" data-reveal>
            <h3>在线试读</h3>
            <p>公开体验站筹备中:将开放仙侠样章试读,无需安装、无需注册。可先下载桌面版或浏览功能导览。</p>
            <a class="dl-go" :href="withBase('/features')">先看功能导览 →</a>
          </div>
        </div>
        <p class="qq-note" data-reveal>
          <img :src="qqQr" alt="jarvis-write QQ 交流群二维码" width="64" height="64" loading="lazy" />
          <span>想先摸线上版?QQ 群 <b>1006352530</b> 进群领邀请码,免部署直接试用。</span>
        </p>
        <footer class="lw-footer" data-reveal>
          <span class="foot-brand"><BrandMark :size="22" class="foot-mark" />jarvis-write · AI 长篇小说工作台</span>
          <nav class="foot-links" aria-label="页脚导航">
            <a :href="withBase('/features')">功能导览</a>
            <a :href="withBase('/00-overview')">设计文档</a>
            <a :href="REPO_URL + '/blob/main/CHANGELOG.md'" target="_blank" rel="noopener">更新日志</a>
            <a :href="REPO_URL" target="_blank" rel="noopener">GitHub</a>
          </nav>
          <span class="foot-meta">Apache-2.0 · 用 AI 写长篇,但写得像人</span>
        </footer>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* ---------- 设计令牌:草蛇灰线 · 灰宣旧金 ---------- */
.lw {
  --lw-paper: #f2f1ec;        /* 灰宣纸底 */
  --lw-card: #fbfaf5;
  --lw-line: #e0ded4;
  --lw-text: #2b2a26;         /* 墨 */
  --lw-muted: #6e6c62;
  --lw-accent: #33454e;       /* 墨青(主色) */
  --lw-accent-deep: #26343c;
  --lw-accent-bright: #5b7280;
  --lw-kraft-gold: #b08d4f;   /* 旧金(故事线的颜色) */
  --lw-deep: #24292e;         /* 深墨(收尾区) */
  --lw-deep-line: rgba(240, 239, 232, 0.14);
  --lw-deep-text: #f0efe9;
  --lw-deep-muted: #a5a39a;
  --lw-serif: 'Noto Serif SC', 'Source Han Serif SC', 'Songti SC', 'STSong', 'SimSun', serif;

  background: var(--lw-paper);
  color: var(--lw-text);
  font-size: 16px;
  line-height: 1.75;
  -webkit-font-smoothing: antialiased;
}

.lw :deep(*) {
  box-sizing: border-box;
}

.lw :where(a, button):focus-visible {
  outline: 2px solid var(--lw-accent);
  outline-offset: 2px;
  border-radius: 4px;
}

.lw-shell {
  max-width: 1180px;
  margin: 0 auto;
  padding: 0 28px;
}

/* ---------- 通用元素 ---------- */
.kicker {
  font-size: 12.5px;
  font-weight: 700;
  letter-spacing: 0.3em;
  color: var(--lw-muted);
  margin: 0 0 18px;
}

.kicker::before {
  content: '—— ';
  color: var(--lw-accent);
}

.kicker.accent {
  color: var(--lw-accent);
}

.kicker.gold {
  color: #d9bc7e;
}

.band-title {
  font-family: var(--lw-serif);
  font-weight: 900;
  font-size: clamp(30px, 4.4vw, 52px);
  line-height: 1.22;
  letter-spacing: 0.01em;
  margin: 0 0 18px;
}

.band-title.light {
  color: var(--lw-deep-text);
}

.band-sub {
  max-width: 640px;
  color: var(--lw-muted);
  font-size: 16.5px;
  margin: 0 0 34px;
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border-radius: 10px;
  font-weight: 600;
  text-decoration: none;
  white-space: nowrap;
  transition: all 0.2s ease;
}

.btn.lg {
  padding: 13px 24px;
  font-size: 16px;
}

.btn.sm {
  padding: 8px 16px;
  font-size: 13.5px;
}

.btn.solid {
  background: var(--lw-accent);
  color: #f2f1ea;
}

.btn.solid:hover {
  background: var(--lw-accent-deep);
  transform: translateY(-1px);
}

.btn.line {
  border: 1px solid rgba(43, 42, 38, 0.28);
  color: var(--lw-text);
}

.btn.line:hover {
  border-color: var(--lw-accent);
  color: var(--lw-accent);
}

.btn.quiet {
  border: 1px solid transparent;
  color: var(--lw-muted);
}

.btn.quiet:hover {
  color: var(--lw-text);
  border-color: var(--lw-line);
}

/* ---------- 导航 ---------- */
.lw-nav {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 50;
  background: rgba(242, 241, 236, 0.88);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid transparent;
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
}

.lw.scrolled .lw-nav {
  border-bottom-color: var(--lw-line);
  box-shadow: 0 6px 24px -18px rgba(43, 42, 38, 0.4);
}

.nav-inner {
  display: flex;
  align-items: center;
  gap: 28px;
  height: 64px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
  margin-right: auto;
}

.brand-mark {
  display: block;
  border-radius: 8px;
  box-shadow: 0 4px 12px -6px rgba(38, 52, 60, 0.7);
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.brand-text b {
  color: var(--lw-text);
  font-size: 15.5px;
  letter-spacing: 0.02em;
}

.brand-text i {
  font-style: normal;
  color: var(--lw-muted);
  font-size: 11px;
  letter-spacing: 0.14em;
}

.nav-links {
  display: flex;
  gap: 22px;
}

.nav-links a {
  color: var(--lw-muted);
  text-decoration: none;
  font-size: 14px;
  transition: color 0.2s ease;
}

.nav-links a:hover {
  color: var(--lw-accent);
}

.nav-cta {
  display: flex;
  gap: 10px;
}

/* ---------- 首屏 ---------- */
.hero {
  background:
    radial-gradient(1100px 480px at 82% -12%, rgba(58, 74, 84, 0.08), transparent 62%),
    repeating-linear-gradient(90deg, rgba(43, 42, 38, 0.03) 0 1px, transparent 1px 72px),
    linear-gradient(180deg, #f5f4ef 0%, var(--lw-paper) 100%);
  padding: 128px 0 92px;
  overflow: hidden;
}

.hero-inner {
  display: grid;
  grid-template-columns: minmax(0, 6fr) minmax(0, 6fr);
  gap: 48px;
  align-items: center;
}

.hero .kicker {
  color: var(--lw-accent);
}

.hero-title {
  font-family: var(--lw-serif);
  font-weight: 900;
  font-size: clamp(36px, 4.2vw, 58px);
  line-height: 1.22;
  letter-spacing: 0.01em;
  margin: 0 0 22px;
}

.hero-title em {
  font-style: normal;
  color: var(--lw-accent-deep);
  position: relative;
}

.hero-title em::after {
  content: '';
  position: absolute;
  left: 2%;
  right: 2%;
  bottom: 0.05em;
  height: 0.12em;
  background: rgba(176, 141, 79, 0.32);
  z-index: -1;
}

.hero-sub {
  font-size: 17px;
  color: var(--lw-muted);
  max-width: 520px;
  margin: 0 0 30px;
}

.hero-sub b {
  color: var(--lw-text);
}

.cta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 22px;
}

.hero-meta {
  font-size: 13px;
  color: rgba(110, 108, 98, 0.75);
  margin: 0;
}

.hero-visual {
  position: relative;
}

.shot-frame {
  margin: 0;
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid var(--lw-line);
  box-shadow: 0 36px 80px -44px rgba(43, 42, 38, 0.55);
  background: var(--lw-card);
  transform: perspective(1600px) rotateY(-3deg) rotateX(0.6deg);
}

.shot-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  background: #eae8e0;
  border-bottom: 1px solid var(--lw-line);
}

.shot-bar i {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #cfcdbc;
}

.shot-bar span {
  margin-left: 10px;
  font-size: 12px;
  color: var(--lw-muted);
}

.shot-frame img {
  display: block;
  width: 100%;
  height: auto;
}

.float-card {
  position: absolute;
  background: var(--lw-card);
  color: var(--lw-text);
  border-radius: 10px;
  padding: 10px 14px;
  box-shadow: 0 18px 42px -22px rgba(43, 42, 38, 0.5);
  border: 1px solid var(--lw-line);
  max-width: 250px;
}

.float-card .fc-tag {
  margin: 0 0 2px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--lw-accent);
}

.float-card .fc-body {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--lw-muted);
}

.float-card b {
  color: var(--lw-text);
}

.fc-a {
  left: -26px;
  bottom: 44px;
  transform: rotate(-2deg);
}

.fc-b {
  right: -18px;
  top: 40px;
  transform: rotate(1.6deg);
}

/* ---------- 区块通用 ---------- */
.band {
  padding: 96px 0;
  background: var(--lw-paper);
}

#ideas,
#flavor,
#engines,
#download {
  scroll-margin-top: 76px;
}

/* ---------- 三理念 ---------- */
.ideas-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  margin-top: 44px;
}

.idea {
  background: var(--lw-card);
  border: 1px solid var(--lw-line);
  border-radius: 14px;
  padding: 30px 28px;
  position: relative;
}

.idea-no {
  position: absolute;
  top: 22px;
  right: 24px;
  font-family: var(--lw-serif);
  font-size: 13px;
  letter-spacing: 0.1em;
  color: var(--lw-kraft-gold);
  opacity: 0.9;
}

.idea h3 {
  font-family: var(--lw-serif);
  font-weight: 900;
  font-size: 24px;
  margin: 0 0 10px;
}

.idea p {
  margin: 0;
  color: var(--lw-muted);
  font-size: 14.5px;
}

/* ---------- 去 AI 味(牛皮纸稿面) ---------- */
.flavor-band {
  background:
    repeating-linear-gradient(0deg, transparent 0 30px, rgba(90, 86, 72, 0.06) 30px 31px),
    linear-gradient(180deg, #edece3 0%, #e9e7dc 100%);
}

.flavor-band {
  --lw-card: #faf9f2;
  --lw-line: #dcd8c8;
  --lw-muted: #6e6c60;
}

.flavor-band .lw-shell {
  max-width: 860px;
}

.flavor-band .band-sub,
.flavor-band .band-note {
  color: #6e6c60;
}

/* ---------- 核心引擎 ---------- */
.engines-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  margin-top: 44px;
}

.engine {
  background: var(--lw-card);
  border: 1px solid var(--lw-line);
  border-radius: 14px;
  padding: 26px 26px 28px;
}

.engine-seal {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 9px;
  background: rgba(51, 69, 78, 0.09);
  border: 1px solid rgba(51, 69, 78, 0.35);
  color: var(--lw-accent);
  font-family: var(--lw-serif);
  font-weight: 900;
  font-size: 17px;
  margin-bottom: 16px;
}

.engine h3 {
  font-family: var(--lw-serif);
  font-weight: 700;
  font-size: 19px;
  margin: 0 0 8px;
}

.engine p {
  margin: 0;
  color: var(--lw-muted);
  font-size: 14px;
  line-height: 1.8;
}

/* ---------- 界面速览 ---------- */
.shots {
  padding-top: 20px;
}

.shots-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 22px;
}

.shot-card {
  margin: 0;
  background: var(--lw-card);
  border: 1px solid var(--lw-line);
  border-radius: 14px;
  overflow: hidden;
}

.shot-card img {
  display: block;
  width: 100%;
  height: auto;
  border-bottom: 1px solid var(--lw-line);
}

.shot-card figcaption {
  padding: 16px 20px;
  font-size: 13.5px;
  color: var(--lw-muted);
  line-height: 1.7;
}

.shot-card figcaption b {
  display: block;
  font-family: var(--lw-serif);
  font-size: 16px;
  color: var(--lw-text);
  margin-bottom: 4px;
}

/* ---------- 下载 + 收尾(深松绿收尾) ---------- */
.final {
  background:
    radial-gradient(900px 420px at 50% 0%, rgba(151, 124, 74, 0.12), transparent 65%),
    var(--lw-deep);
  color: var(--lw-deep-text);
  padding-bottom: 0;
}

.final .band-sub {
  color: var(--lw-deep-muted);
}

.dl-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  margin: 48px 0 22px;
}

.dl-card {
  display: flex;
  flex-direction: column;
  background: rgba(240, 239, 232, 0.05);
  border: 1px solid var(--lw-deep-line);
  border-radius: 14px;
  padding: 26px 26px 24px;
  text-decoration: none;
  color: inherit;
  transition: border-color 0.2s ease, transform 0.2s ease;
}

.dl-card:hover {
  border-color: rgba(201, 168, 106, 0.6);
  transform: translateY(-2px);
}

.dl-card h3 {
  font-family: var(--lw-serif);
  font-weight: 700;
  font-size: 19px;
  margin: 0 0 8px;
  color: var(--lw-deep-text);
}

.dl-card p {
  margin: 0 0 18px;
  font-size: 13.5px;
  color: var(--lw-deep-muted);
  flex: 1;
}

.dl-go {
  font-size: 14px;
  font-weight: 600;
  color: #d9bc7e;
}

.dl-cred {
  font-size: 13px !important;
  color: var(--lw-deep-muted) !important;
}

.dl-cred code {
  font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
  background: rgba(240, 239, 232, 0.12);
  border-radius: 5px;
  padding: 2px 7px;
  color: var(--lw-deep-text);
  font-size: 12.5px;
}

.qq-note {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 0 0 72px;
  padding: 12px 16px;
  border: 1px dashed rgba(240, 239, 232, 0.26);
  border-radius: 12px;
  font-size: 13.5px;
  color: var(--lw-deep-muted);
}

.qq-note img {
  border-radius: 8px;
  display: block;
}

.qq-note b {
  color: var(--lw-deep-text);
}

.lw-footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px 28px;
  padding: 26px 0 30px;
  border-top: 1px solid rgba(240, 239, 232, 0.12);
  font-size: 13px;
  color: rgba(240, 239, 232, 0.55);
}

.foot-brand {
  margin-right: auto;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--lw-serif);
  font-weight: 700;
  color: rgba(240, 239, 232, 0.8);
}

.foot-mark {
  display: block;
  border-radius: 5px;
}

.foot-links {
  display: flex;
  gap: 20px;
}

.foot-links a {
  color: rgba(240, 239, 232, 0.6);
  text-decoration: none;
}

.foot-links a:hover {
  color: #fff;
}

.foot-meta {
  color: rgba(240, 239, 232, 0.38);
}

/* ---------- 进场动效 ---------- */
[data-reveal] {
  opacity: 0;
  transform: translateY(18px);
  transition: opacity 0.6s ease, transform 0.6s ease;
}

[data-reveal].in {
  opacity: 1;
  transform: none;
}

.hero [data-reveal] {
  transition-delay: 0.05s;
}

/* ---------- 响应式 ---------- */
@media (max-width: 1024px) {
  .hero-inner {
    grid-template-columns: 1fr;
    gap: 44px;
  }

  .hero-visual {
    max-width: 680px;
  }

  .fc-a {
    left: 8px;
  }

  .fc-b {
    right: 8px;
  }

  .ideas-grid,
  .engines-grid,
  .dl-grid {
    grid-template-columns: 1fr 1fr;
  }

  .shots-grid {
    grid-template-columns: 1fr;
  }

  .nav-links {
    display: none;
  }
}

@media (max-width: 640px) {
  .lw-shell {
    padding: 0 18px;
  }

  .hero {
    padding: 104px 0 72px;
  }

  .band {
    padding: 68px 0;
  }

  .ideas-grid,
  .engines-grid,
  .dl-grid {
    grid-template-columns: 1fr;
  }

  .cta-row .btn.lg {
    width: 100%;
  }

  .nav-cta .line {
    display: none;
  }

  .float-card {
    display: none;
  }

  .qq-note {
    align-items: flex-start;
  }
}

/* ---------- 无动效偏好 ---------- */
@media (prefers-reduced-motion: reduce) {
  [data-reveal] {
    opacity: 1;
    transform: none;
    transition: none;
  }

  .shot-frame {
    transform: none;
  }
}
</style>
