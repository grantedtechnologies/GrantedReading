-- Add one answer per teacher per question.
-- Inspected production on 2026-11-05: Answer has 4 rows and
-- zero (teacherid, questionid) groups with COUNT(*) > 1, so this file
-- does not delete anything.
-- If the ALTER fails because duplicates appeared later, stop. Do not
-- delete rows until that set has been reviewed.
-- Existing indexes answer_teacherid_foreign and answer_questionid_foreign
-- stay in place.
-- Safe to re-run: the ALTER is skipped when the index already exists.

SET @index_exists = (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'Answer'
      AND INDEX_NAME = 'answer_teacherid_questionid_unique'
);

SET @ddl = IF(
    @index_exists = 0,
    'ALTER TABLE `Answer` ADD UNIQUE INDEX `answer_teacherid_questionid_unique` (`teacherid`, `questionid`), ALGORITHM=INPLACE, LOCK=NONE',
    'DO 0'
);

PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
