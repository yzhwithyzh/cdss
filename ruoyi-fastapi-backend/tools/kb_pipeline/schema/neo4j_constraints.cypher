// =====================================================================
// Neo4j 图模型：约束与索引（Community 版：仅唯一性约束 + 普通/全文索引）
// 所有节点以 PG 的 uid 为身份；可重复执行（IF NOT EXISTS）
// =====================================================================

// ---- 唯一身份 ----
CREATE CONSTRAINT concept_uid        IF NOT EXISTS FOR (n:Concept)            REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT concept_code       IF NOT EXISTS FOR (n:Concept)            REQUIRE n.code IS UNIQUE;
CREATE CONSTRAINT defvar_id          IF NOT EXISTS FOR (n:DefinitionVariant)  REQUIRE n.variant_id IS UNIQUE;
CREATE CONSTRAINT document_uid       IF NOT EXISTS FOR (n:Document)           REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT study_uid          IF NOT EXISTS FOR (n:Study)              REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT population_uid     IF NOT EXISTS FOR (n:Population)         REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT arm_uid            IF NOT EXISTS FOR (n:Arm)                REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT outcome_uid        IF NOT EXISTS FOR (n:Outcome)            REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT finding_uid        IF NOT EXISTS FOR (n:Finding)            REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT result_uid         IF NOT EXISTS FOR (n:Result)             REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT em_uid             IF NOT EXISTS FOR (n:EffectModification) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT eq_uid             IF NOT EXISTS FOR (n:EvidenceQuestion)   REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT claim_uid          IF NOT EXISTS FOR (n:Claim)              REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT synthesis_uid      IF NOT EXISTS FOR (n:Synthesis)          REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT guideline_uid      IF NOT EXISTS FOR (n:Guideline)          REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT rec_uid            IF NOT EXISTS FOR (n:Recommendation)     REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT rule_uid           IF NOT EXISTS FOR (n:ClinicalRule)       REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT assertion_uid      IF NOT EXISTS FOR (n:Assertion)          REQUIRE n.uid IS UNIQUE;

// ---- 常用过滤 ----
CREATE INDEX concept_type      IF NOT EXISTS FOR (n:Concept)    ON (n.concept_type);
CREATE INDEX document_record   IF NOT EXISTS FOR (n:Document)   ON (n.record_id);
CREATE INDEX finding_status    IF NOT EXISTS FOR (n:Finding)    ON (n.review_status, n.statement_role);
CREATE INDEX population_age    IF NOT EXISTS FOR (n:Population) ON (n.age_lower, n.age_upper);
CREATE INDEX population_setting IF NOT EXISTS FOR (n:Population) ON (n.indication_setting);
CREATE INDEX study_design      IF NOT EXISTS FOR (n:Study)      ON (n.design_coarse);

// ---- 概念名称/别名全文检索（中英文；cjk 分析器支持中文）----
CREATE FULLTEXT INDEX concept_names IF NOT EXISTS
FOR (n:Concept) ON EACH [n.name_zh, n.name_en, n.aliases_text]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } };
