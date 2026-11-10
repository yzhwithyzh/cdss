-- ----------------------------
-- 文献库菜单与权限（菜单ID从 2000 起，避开框架内置菜单）
-- ----------------------------
insert into sys_menu (menu_id, menu_name, parent_id, order_num, path, component, query, route_name, is_frame, is_cache, menu_type, visible, status, perms, icon, create_by, create_time, remark)
values
    (2000, '文献库', 0, 0, 'literature', null, null, '', 1, 0, 'M', '0', '0', '', 'documentation', 'admin', now(), '文献库目录'),
    (2001, '文献题录', 2000, 1, 'record', 'literature/record/index', null, 'LiteratureRecord', 1, 0, 'C', '0', '0', 'literature:record:list', 'list', 'admin', now(), '文献题录菜单'),
    (2002, '题录查询', 2001, 1, '', null, null, '', 1, 0, 'F', '0', '0', 'literature:record:query', '#', 'admin', now(), ''),
    (2003, '研究类型审核', 2001, 2, '', null, null, '', 1, 0, 'F', '0', '0', 'literature:design:review', '#', 'admin', now(), ''),
    (2004, '题录修改', 2001, 3, '', null, null, '', 1, 0, 'F', '0', '0', 'literature:record:edit', '#', 'admin', now(), ''),
    (2005, '题录删除', 2001, 4, '', null, null, '', 1, 0, 'F', '0', '0', 'literature:record:remove', '#', 'admin', now(), '')
on conflict (menu_id) do nothing;

select setval('sys_menu_menu_id_seq', greatest((select max(menu_id) from sys_menu), 2005));
