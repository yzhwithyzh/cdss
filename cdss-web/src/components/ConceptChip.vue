<template>
  <router-link v-if="id" :to="`/concept/${id}`" class="concept-chip" :title="label(CONCEPT_TYPE, type)">
    <span class="dot" :style="{ background: CONCEPT_COLOR[type] || '#93a1aa' }" />{{ name }}<span class="n" v-if="n != null">{{ n }}</span>
  </router-link>
  <span v-else class="pill">{{ name }}</span>
</template>

<script setup>
import { CONCEPT_COLOR, CONCEPT_TYPE, label } from "@/labels";
defineProps({ id: [Number, String], name: String, type: String, n: Number });
</script>
