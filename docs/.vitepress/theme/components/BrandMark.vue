<script setup lang="ts">
// 品牌 logo:「故事线」——三条章节线之间,一条金色故事线贯穿不断,线头一颗墨点。
// 章节在走,线不断:即产品的核心承诺(长程一致性/伏笔不丢)。
// 「草蛇灰线,伏脉千里」:章节线是灰,故事线是金。
withDefaults(defineProps<{ size?: number }>(), { size: 34 })
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 64 64"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <rect x="2" y="2" width="60" height="60" rx="15" fill="#33454e" />
    <!-- 章节线:灰,章节在排布 -->
    <line x1="15" y1="21.5" x2="34" y2="21.5" stroke="#ece9e0" stroke-width="4" stroke-linecap="round" opacity="0.5" />
    <line x1="15" y1="32" x2="41" y2="32" stroke="#ece9e0" stroke-width="4" stroke-linecap="round" opacity="0.75" />
    <line x1="15" y1="42.5" x2="29" y2="42.5" stroke="#ece9e0" stroke-width="4" stroke-linecap="round" opacity="0.5" />
    <!-- 故事线:一条金线贯穿三行,线头即落笔处 -->
    <path d="M12 51 C 27 45, 25 25, 51 14.5" stroke="#d4ab5f" stroke-width="4.5" stroke-linecap="round" fill="none" />
    <circle cx="51" cy="14.5" r="3.6" fill="#d4ab5f" />
  </svg>
</template>
