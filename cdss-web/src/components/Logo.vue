<template>
  <!-- 标志：证据金字塔（三层文献）+ 顶端的结论节点 -->
  <svg :width="size" :height="size" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <defs>
      <linearGradient :id="gid" x1="4" y1="4" x2="44" y2="44" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#1d6f7a" />
        <stop offset="1" stop-color="#0e434c" />
      </linearGradient>
    </defs>
    <rect width="48" height="48" rx="12" :fill="`url(#${gid})`" />
    <rect x="9" y="31" width="30" height="5.5" rx="2" fill="#fff" fill-opacity=".55" />
    <rect x="13.5" y="23.5" width="21" height="5.5" rx="2" fill="#fff" fill-opacity=".78" />
    <rect x="18" y="16" width="12" height="5.5" rx="2" fill="#fff" fill-opacity=".95" />
    <circle cx="24" cy="10.5" r="3.2" fill="#e3a35c" />
  </svg>
</template>

<script setup>
defineProps({ size: { type: [Number, String], default: 36 } });
const gid = "kb-g-" + Math.random().toString(36).slice(2, 7);
</script>
