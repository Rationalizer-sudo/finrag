-- ============================================================================
--  FinRAG · 金融制度智能问答系统 —— MySQL 结构化数据建表脚本
--  数据库：MySQL 8.0+    字符集：utf8mb4    排序规则：utf8mb4_unicode_ci
--
--  与 ORM 模型的对应关系（backend/app/db/models.py）：
--    kb_document       <-> KnowledgeDocument
--    kb_chunk          <-> KnowledgeChunk      （条文级知识点，Milvus 存其 embedding）
--    kb_tag            <-> KnowledgeTag
--    kb_document_tag   <-> DocumentTag
--    kb_chunk_tag      <-> ChunkTag
--    qa_history        <-> QaHistory
--    qa_favorite       <-> QaFavorite
--    kb_ingest_task    <-> IngestTask          （增量更新审计）
--    sys_user          <-> SysUser
--
--  执行方式（任选其一）：
--    1) python scripts/init_mysql.py
--    2) mysql -u root -p < sql/01_schema.sql
--    3) Docker 首次启动时由 /docker-entrypoint-initdb.d/ 自动执行
-- ============================================================================

CREATE DATABASE IF NOT EXISTS `finrag`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE `finrag`;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ---------------------------------------------------------------------------
-- 1. 制度文档主表
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS `kb_document`;
CREATE TABLE `kb_document` (
    `id`             BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
    `doc_no`         VARCHAR(128) NOT NULL DEFAULT ''  COMMENT '文号，如 银发〔2020〕15号',
    `title`          VARCHAR(512) NOT NULL             COMMENT '制度名称',
    `issuer`         VARCHAR(255) NOT NULL DEFAULT ''  COMMENT '发文机关',
    `doc_type`       VARCHAR(32)  NOT NULL DEFAULT '规范性文件'
        COMMENT '文件类型：法律/行政法规/司法解释/部门规章/规范性文件/自律规则/国家标准/其他',
    `business_line`  VARCHAR(32)  NOT NULL DEFAULT '综合'
        COMMENT '业务条线：银行/证券/保险/基金/支付结算/反洗钱/信托/期货/综合',
    `status`         VARCHAR(16)  NOT NULL DEFAULT '状态待核'
        COMMENT '时效状态：现行有效/尚未生效/已修订/已废止/状态待核',
    `publish_date`   DATE         NULL                 COMMENT '发布日期',
    `effective_date` DATE         NULL                 COMMENT '生效（施行）日期',
    `expiry_date`    DATE         NULL                 COMMENT '失效日期',
    `source_url`     VARCHAR(512) NOT NULL DEFAULT ''  COMMENT '公开来源链接（数据安全：仅公开数据）',
    `file_name`      VARCHAR(255) NOT NULL DEFAULT ''  COMMENT '原始文件名',
    `file_type`      VARCHAR(16)  NOT NULL DEFAULT ''  COMMENT '文件扩展名',
    `file_hash`      VARCHAR(64)  NULL                 COMMENT '文件内容 SHA-256，用于增量入库判重',
    `ocr_used`       TINYINT(1)   NOT NULL DEFAULT 0   COMMENT '是否经过 OCR 解析',
    `parser_name`    VARCHAR(64)  NOT NULL DEFAULT ''  COMMENT '使用的解析器标识',
    `chunk_count`    INT          NOT NULL DEFAULT 0   COMMENT '条文（知识点）数量',
    `version`        INT          NOT NULL DEFAULT 1   COMMENT '修订版本号，重复入库时自增',
    `remark`         VARCHAR(512) NOT NULL DEFAULT ''  COMMENT '备注',
    `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_document_hash` (`file_hash`),
    KEY `idx_document_status` (`status`),
    KEY `idx_document_business` (`business_line`),
    KEY `idx_document_effective` (`effective_date`),
    KEY `idx_document_type` (`doc_type`),
    FULLTEXT KEY `ft_document_title` (`title`, `issuer`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '金融制度文档主表';

-- ---------------------------------------------------------------------------
-- 2. 条文（知识点）表
--    向量主键 milvus_pk = id，检索命中后按此回查正文（结构化 + 向量协同查询）
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS `kb_chunk`;
CREATE TABLE `kb_chunk` (
    `id`             BIGINT   NOT NULL AUTO_INCREMENT COMMENT '主键，同时作为 Milvus 主键',
    `document_id`    BIGINT   NOT NULL             COMMENT '所属文档 ID',
    `chunk_index`    INT      NOT NULL DEFAULT 0   COMMENT '文内顺序号',
    `clause_no`      VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '条文号，如 第十二条',
    `chapter`        VARCHAR(255) NOT NULL DEFAULT '' COMMENT '所属章节，如 第三章 流动性风险管理',
    `article_title`  VARCHAR(255) NOT NULL DEFAULT '' COMMENT '条文小标题（【】内内容）',
    `content`        MEDIUMTEXT    NOT NULL           COMMENT '条文正文',
    `content_hash`   VARCHAR(64)   NOT NULL           COMMENT '正文 SHA-256（文档内去重）',
    `char_count`     INT      NOT NULL DEFAULT 0   COMMENT '字符数',
    `token_count`    INT      NOT NULL DEFAULT 0   COMMENT '分词后词数',
    `status`         VARCHAR(16)  NOT NULL DEFAULT '状态待核'
        COMMENT '冗余时效状态，检索过滤用（与文档同源）',
    `effective_date` DATE     NULL                 COMMENT '生效日期',
    `expiry_date`    DATE     NULL                 COMMENT '失效日期',
    `effective_ts`   BIGINT   NOT NULL DEFAULT 0   COMMENT '生效时间戳(秒)，0=不限；用于区间过滤',
    `expiry_ts`      BIGINT   NOT NULL DEFAULT 0   COMMENT '失效时间戳(秒)，0=长期有效',
    `business_line`  VARCHAR(32)  NOT NULL DEFAULT '综合' COMMENT '业务条线（冗余，便于过滤）',
    `doc_type`       VARCHAR(32)  NOT NULL DEFAULT '规范性文件' COMMENT '文件类型（冗余）',
    `milvus_pk`      BIGINT   NULL                 COMMENT 'Milvus 主键（= id）',
    `created_at`     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_chunk_doc_hash` (`document_id`, `content_hash`),
    KEY `idx_chunk_document` (`document_id`),
    KEY `idx_chunk_status` (`status`),
    KEY `idx_chunk_business` (`business_line`),
    KEY `idx_chunk_validity` (`status`, `effective_ts`, `expiry_ts`),
    FULLTEXT KEY `ft_chunk_content` (`content`) WITH PARSER ngram,
    CONSTRAINT `fk_chunk_document` FOREIGN KEY (`document_id`)
        REFERENCES `kb_document` (`id`) ON DELETE CASCADE
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '条文级知识点表';

-- ---------------------------------------------------------------------------
-- 3. 知识点标签
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS `kb_tag`;
CREATE TABLE `kb_tag` (
    `id`          INT          NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name`        VARCHAR(64)  NOT NULL             COMMENT '标签名',
    `category`    VARCHAR(32)  NOT NULL DEFAULT '主题'
        COMMENT '标签分类：业务条线/主题/风险类型',
    `description` VARCHAR(255) NOT NULL DEFAULT ''  COMMENT '标签说明',
    `ref_count`   INT          NOT NULL DEFAULT 0   COMMENT '引用计数',
    `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_tag_name_category` (`name`, `category`),
    KEY `idx_tag_category` (`category`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '知识点标签表';

DROP TABLE IF EXISTS `kb_document_tag`;
CREATE TABLE `kb_document_tag` (
    `document_id` BIGINT NOT NULL COMMENT '文档 ID',
    `tag_id`      INT    NOT NULL COMMENT '标签 ID',
    PRIMARY KEY (`document_id`, `tag_id`),
    KEY `idx_doc_tag_tag` (`tag_id`),
    CONSTRAINT `fk_doc_tag_document` FOREIGN KEY (`document_id`)
        REFERENCES `kb_document` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_doc_tag_tag` FOREIGN KEY (`tag_id`)
        REFERENCES `kb_tag` (`id`) ON DELETE CASCADE
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '文档-标签关联表';

DROP TABLE IF EXISTS `kb_chunk_tag`;
CREATE TABLE `kb_chunk_tag` (
    `chunk_id` BIGINT NOT NULL COMMENT '条文 ID',
    `tag_id`   INT    NOT NULL COMMENT '标签 ID',
    PRIMARY KEY (`chunk_id`, `tag_id`),
    KEY `idx_chunk_tag_tag` (`tag_id`),
    CONSTRAINT `fk_chunk_tag_chunk` FOREIGN KEY (`chunk_id`)
        REFERENCES `kb_chunk` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_chunk_tag_tag` FOREIGN KEY (`tag_id`)
        REFERENCES `kb_tag` (`id`) ON DELETE CASCADE
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '条文-标签关联表';

-- ---------------------------------------------------------------------------
-- 4. 问答历史（差异化要求：问答历史记录）
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS `qa_history`;
CREATE TABLE `qa_history` (
    `id`                BIGINT   NOT NULL AUTO_INCREMENT COMMENT '主键',
    `session_id`        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '会话标识',
    `user_id`           VARCHAR(64)  NOT NULL DEFAULT 'anonymous' COMMENT '用户标识',
    `question`          TEXT         NOT NULL            COMMENT '用户问题',
    `answer`            MEDIUMTEXT   NULL                COMMENT '模型回答',
    `retrieval_mode`    VARCHAR(16)  NOT NULL DEFAULT 'hybrid'
        COMMENT '检索模式：vector/bm25/hybrid',
    `top_k`             INT          NOT NULL DEFAULT 5  COMMENT '召回条数',
    `evidence_count`    INT          NOT NULL DEFAULT 0  COMMENT '实际证据条数',
    `latency_ms`        INT          NOT NULL DEFAULT 0  COMMENT '端到端耗时(毫秒)',
    `credibility_score` FLOAT        NOT NULL DEFAULT 0  COMMENT '可信度评分 0~100',
    `credibility_label` VARCHAR(16)  NOT NULL DEFAULT '' COMMENT '可信度等级',
    `citations`         JSON         NULL                COMMENT '条文溯源列表',
    `has_answer`        TINYINT(1)   NOT NULL DEFAULT 1  COMMENT '是否基于知识库作答',
    `created_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_history_user_time` (`user_id`, `created_at`),
    KEY `idx_history_session` (`session_id`),
    KEY `idx_history_score` (`credibility_score`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '问答历史表';

-- ---------------------------------------------------------------------------
-- 5. 收藏（差异化要求：收藏功能）
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS `qa_favorite`;
CREATE TABLE `qa_favorite` (
    `id`                BIGINT   NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`           VARCHAR(64)  NOT NULL DEFAULT 'anonymous' COMMENT '用户标识',
    `history_id`        BIGINT   NULL                COMMENT '来源历史记录 ID',
    `question`          TEXT         NOT NULL        COMMENT '问题',
    `answer`            MEDIUMTEXT   NULL            COMMENT '回答',
    `note`              VARCHAR(512) NOT NULL DEFAULT '' COMMENT '用户备注',
    `citations`         JSON         NULL            COMMENT '条文溯源列表',
    `credibility_score` FLOAT        NOT NULL DEFAULT 0 COMMENT '可信度评分',
    `created_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_favorite_user_history` (`user_id`, `history_id`),
    KEY `idx_favorite_user` (`user_id`),
    CONSTRAINT `fk_favorite_history` FOREIGN KEY (`history_id`)
        REFERENCES `qa_history` (`id`) ON DELETE SET NULL
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '问答收藏表';

-- ---------------------------------------------------------------------------
-- 6. 入库任务（创新点：知识库增量更新审计）
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS `kb_ingest_task`;
CREATE TABLE `kb_ingest_task` (
    `id`               BIGINT   NOT NULL AUTO_INCREMENT COMMENT '主键',
    `task_no`          VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '任务号',
    `file_name`        VARCHAR(255) NOT NULL DEFAULT '' COMMENT '文件名',
    `file_hash`        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '文件哈希',
    `document_id`      BIGINT   NULL                 COMMENT '产出文档 ID',
    `status`           VARCHAR(16)  NOT NULL DEFAULT 'pending'
        COMMENT 'pending/running/success/partial/failed/skipped',
    `stage`            VARCHAR(32)  NOT NULL DEFAULT ''
        COMMENT '当前阶段：parsing/cleaning/extracting-metadata/splitting/persisting/embedding/done',
    `total_chunks`     INT      NOT NULL DEFAULT 0   COMMENT '计划条文数',
    `processed_chunks` INT      NOT NULL DEFAULT 0   COMMENT '已处理条文数',
    `ocr_used`         TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否使用 OCR',
    `cost_ms`          INT      NOT NULL DEFAULT 0   COMMENT '耗时(毫秒)',
    `message`          TEXT     NULL                 COMMENT '结果说明/错误信息',
    `created_at`       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_task_no` (`task_no`),
    KEY `idx_task_hash` (`file_hash`),
    KEY `idx_task_status` (`status`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '知识库入库任务表';

-- ---------------------------------------------------------------------------
-- 7. 系统用户（轻量账号，用于问答历史/收藏的用户隔离）
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS `sys_user`;
CREATE TABLE `sys_user` (
    `id`            INT          NOT NULL AUTO_INCREMENT COMMENT '主键',
    `username`      VARCHAR(64)  NOT NULL             COMMENT '登录名',
    `password_hash` VARCHAR(255) NOT NULL             COMMENT '口令哈希（bcrypt）',
    `display_name`  VARCHAR(64)  NOT NULL DEFAULT ''  COMMENT '显示名',
    `role`          VARCHAR(16)  NOT NULL DEFAULT 'user' COMMENT 'user/admin',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_username` (`username`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '系统用户表';

-- ---------------------------------------------------------------------------
-- 8. 常用视图：现行有效条文（供数据分析与人工抽检直接使用）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW `v_valid_chunk` AS
SELECT
    c.`id`            AS chunk_id,
    d.`id`            AS document_id,
    d.`title`         AS doc_title,
    d.`doc_no`        AS doc_no,
    d.`issuer`        AS issuer,
    c.`clause_no`     AS clause_no,
    c.`content`       AS content,
    c.`status`        AS status,
    c.`effective_date` AS effective_date,
    c.`expiry_date`   AS expiry_date,
    c.`business_line` AS business_line,
    d.`source_url`    AS source_url
FROM `kb_chunk` c
JOIN `kb_document` d ON d.`id` = c.`document_id`
WHERE c.`status` IN ('现行有效', '尚未生效')
  AND (c.`expiry_ts` = 0 OR c.`expiry_ts` >= UNIX_TIMESTAMP())
  AND (c.`effective_ts` = 0 OR c.`effective_ts` <= UNIX_TIMESTAMP());

SET FOREIGN_KEY_CHECKS = 1;

-- ---------------------------------------------------------------------------
-- 9. 初始化完成提示
-- ---------------------------------------------------------------------------
SELECT
    'FinRAG 数据库初始化完成' AS message,
    (SELECT COUNT(*) FROM information_schema.tables
      WHERE table_schema = 'finrag') AS table_count;
