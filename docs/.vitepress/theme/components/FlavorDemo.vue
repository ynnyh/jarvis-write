<script setup lang="ts">
import { computed, ref } from 'vue'

// 对照组直接取自 backend/app/prompts/style_capsules.py 的 PAIRWISE 定稿门禁规则,
// 官网展示的即产品真实内置的改法,不另编例子。
const PAIRS: Array<{ bad: string; good: string }> = [
  { bad: '他眼中闪过一丝复杂的神色,心里五味杂陈。', good: '他把烟摁灭在桌角,没接话。' },
  { bad: '她感到一阵无法言喻的绝望。', good: '她盯着那只空碗,很久没有动筷子。' },
  { bad: '这一刻,他终于明白了坚持的意义。', good: '他没再说什么,弯腰把散落的工具一件件捡回箱子。' },
  { bad: '月光如水,宛如一层薄纱,仿佛给大地披上了银装。', good: '月亮很亮,院里的青石板泛着白。' },
  { bad: '她沉默片刻,缓缓开口道。', good: '她想了想,说。' },
  { bad: '空气中弥漫着一种难以言喻的紧张气氛。', good: '没人说话。墙上的钟走得很响。' },
  { bad: '他的眼神是坚定的,他的信念是不可动摇的,他的脚步是沉稳的。', good: '他脚步没停,一直走到队伍最前面。' },
]

const fixed = ref<Set<number>>(new Set())

function fix(i: number) {
  const next = new Set(fixed.value)
  next.add(i)
  fixed.value = next
}

function reset() {
  fixed.value = new Set()
}

function fixAll() {
  fixed.value = new Set(PAIRS.map((_, i) => i))
}

const count = computed(() => fixed.value.size)
const done = computed(() => count.value === PAIRS.length)
</script>

<template>
  <div class="flavor">
    <div class="flavor-toolbar">
      <span class="flavor-count">已去味 {{ count }} / {{ PAIRS.length }}</span>
      <div class="flavor-actions">
        <button type="button" class="flavor-btn ghost" @click="reset">重来</button>
        <button type="button" class="flavor-btn solid" @click="fixAll">一键全改</button>
      </div>
    </div>

    <ul class="flavor-list">
      <li
        v-for="(p, i) in PAIRS"
        :key="i"
        class="flavor-row"
        :class="{ fixed: fixed.has(i) }"
      >
        <button
          type="button"
          class="flavor-bad"
          :aria-pressed="fixed.has(i)"
          @click="fix(i)"
        >
          <span class="mark bad">✗</span>
          <s class="bad-text">{{ p.bad }}</s>
        </button>
        <p class="flavor-good" aria-live="polite">
          <span class="mark good">✓</span>
          {{ p.good }}
          <span class="seal" aria-hidden="true">人话</span>
        </p>
      </li>
    </ul>

    <p class="flavor-foot" :class="{ show: done }">
      这还只是规则层。定稿时还有句长节奏、段落结构的统计门禁——超标就定向去味重写、复测收敛,没改好自动回退,绝不落更差的版本。
    </p>
    <p class="flavor-hint" :class="{ hide: done }">点左边带 ✗ 的句子,看门禁要求把它改成什么样。</p>
  </div>
</template>

<style scoped>
.flavor {
  background: var(--lw-card, #fefefe);
  border: 1px solid var(--lw-line, #e4e6e1);
  border-radius: 14px;
  padding: 22px 26px 26px;
  box-shadow: 0 20px 50px -30px rgba(34, 26, 15, 0.35);
}

.flavor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--lw-line, #e4e6e1);
  margin-bottom: 6px;
}

.flavor-count {
  font-family: var(--lw-serif, serif);
  font-weight: 700;
  color: var(--lw-accent, #2f5382);
  letter-spacing: 0.04em;
}

.flavor-actions {
  display: flex;
  gap: 8px;
}

.flavor-btn {
  font-size: 13px;
  line-height: 1;
  padding: 8px 14px;
  border-radius: 999px;
  cursor: pointer;
  border: 1px solid var(--lw-line, #e4e6e1);
  background: transparent;
  color: var(--lw-muted, #5f6f80);
  transition: all 0.2s ease;
}

.flavor-btn.solid {
  background: var(--lw-accent, #2f5382);
  border-color: var(--lw-accent, #2f5382);
  color: #fff7ee;
}

.flavor-btn.ghost:hover {
  border-color: var(--lw-accent, #2f5382);
  color: var(--lw-accent, #2f5382);
}

.flavor-btn.solid:hover {
  filter: brightness(1.08);
}

.flavor-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.flavor-row {
  padding: 13px 2px 12px;
  border-bottom: 1px dashed var(--lw-line, #e4e6e1);
}

.flavor-row:last-child {
  border-bottom: none;
}

.flavor-bad {
  display: flex;
  align-items: baseline;
  gap: 10px;
  width: 100%;
  text-align: left;
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  font: inherit;
  color: inherit;
}

.flavor-bad:hover .bad-text {
  color: var(--lw-accent, #2f5382);
}

.mark {
  flex: none;
  font-weight: 700;
  font-size: 14px;
  width: 18px;
}

.mark.bad {
  color: #b3a893;
}

.mark.good {
  color: var(--lw-accent, #2f5382);
}

.bad-text {
  font-size: 15px;
  color: var(--lw-muted, #5f6f80);
  text-decoration-color: transparent;
  text-decoration-thickness: 2px;
  transition: color 0.25s ease;
}

.flavor-row.fixed .bad-text {
  text-decoration-color: var(--lw-accent, #2f5382);
  color: #a49a88;
}

.flavor-good {
  display: none;
  margin: 8px 0 0 28px;
  font-size: 15px;
  color: var(--lw-text, #22303f);
  align-items: baseline;
  gap: 10px;
  animation: flavor-in 0.35s ease;
}

.flavor-row.fixed .flavor-good {
  display: flex;
}

.seal {
  flex: none;
  margin-left: 4px;
  font-family: var(--lw-serif, serif);
  font-size: 11px;
  font-weight: 700;
  color: var(--lw-accent, #2f5382);
  border: 1.5px solid var(--lw-accent, #2f5382);
  border-radius: 4px;
  padding: 1px 5px;
  transform: rotate(-6deg);
  opacity: 0.85;
}

@keyframes flavor-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

.flavor-foot,
.flavor-hint {
  margin: 16px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
}

.flavor-hint {
  color: var(--lw-muted, #5f6f80);
}

.flavor-foot {
  display: none;
  color: var(--lw-text, #22303f);
  border-left: 3px solid var(--lw-accent, #2f5382);
  padding-left: 12px;
}

.flavor-foot.show {
  display: block;
  animation: flavor-in 0.35s ease;
}

.flavor-hint.hide {
  display: none;
}

@media (max-width: 640px) {
  .flavor {
    padding: 16px 16px 20px;
  }
}
</style>
