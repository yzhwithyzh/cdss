import { createRouter, createWebHistory } from "vue-router";

const routes = [
  { path: "/", name: "home", component: () => import("@/views/Home.vue") },
  { path: "/search", name: "search", component: () => import("@/views/Search.vue") },
  { path: "/record/:id", name: "record", component: () => import("@/views/Record.vue") },
  { path: "/concepts", name: "concepts", component: () => import("@/views/Concepts.vue") },
  { path: "/concept/:id", name: "concept", component: () => import("@/views/Concept.vue") },
  { path: "/question/:code?", name: "question", component: () => import("@/views/Question.vue") },
  { path: "/chat", name: "chat", component: () => import("@/views/Chat.vue") },
  { path: "/compare", name: "compare", component: () => import("@/views/Compare.vue") },
  { path: "/:pathMatch(.*)*", redirect: "/" },
];

export default createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: (to, from, saved) => saved || { top: 0 },
});
