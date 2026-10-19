DROP DATABASE IF EXISTS granteddb;
CREATE DATABASE granteddb
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE granteddb;

DROP TABLE IF EXISTS downloads;
DROP TABLE IF EXISTS versions;
DROP TABLE IF EXISTS words;
DROP TABLE IF EXISTS worksheets;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS processed_webhook_events;
DROP TABLE IF EXISTS subscriptions;
DROP TABLE IF EXISTS Answer;
DROP TABLE IF EXISTS Question;
DROP TABLE IF EXISTS Events;
DROP TABLE IF EXISTS Survey;
DROP TABLE IF EXISTS Pilot;
DROP TABLE IF EXISTS vocabList;
DROP TABLE IF EXISTS vocab_lists;
DROP TABLE IF EXISTS Students;
DROP TABLE IF EXISTS class;
DROP TABLE IF EXISTS districts;
DROP TABLE IF EXISTS Teachers;

CREATE TABLE `Teachers`(
    `teacherid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `fname` VARCHAR(255) NOT NULL,
    `lname` VARCHAR(255) NOT NULL,
    `email` VARCHAR(255) NOT NULL,
    `password` VARCHAR(255) NOT NULL,
    `verified` BOOLEAN NOT NULL,
    `createdAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `country` VARCHAR(255) NOT NULL,
    `state` VARCHAR(255) NOT NULL,
    `city` VARCHAR(255) NOT NULL,
    `district` VARCHAR(255) NOT NULL,
    `school` VARCHAR(255) NOT NULL
);
ALTER TABLE
    `Teachers` ADD UNIQUE `teachers_email_unique`(`email`);
CREATE TABLE `districts`(
    `districtid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `country` VARCHAR(80) NOT NULL,
    `state` VARCHAR(80) NOT NULL,
    `stateCode` CHAR(2) NULL,
    `city` VARCHAR(80) NOT NULL,
    `name` VARCHAR(120) NOT NULL,
    `latitude` DECIMAL(9, 6) NULL,
    `longitude` DECIMAL(9, 6) NULL
);
CREATE INDEX `districts_place_index` ON `districts` (`country`, `state`, `city`);
CREATE INDEX `districts_state_code_index` ON `districts` (`stateCode`, `city`);
CREATE TABLE `Pilot`(
    `pilotid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `districtid` BIGINT UNSIGNED NOT NULL,
    `name` VARCHAR(255) NOT NULL, 
    `school` VARCHAR(255) NOT NULL,
    `code` VARCHAR(255) NOT NULL,
    `startTime` DATETIME NOT NULL,
    `endTime` DATETIME NOT NULL,
    `createdAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT `pilot_window_check` CHECK (`endTime` > `startTime`)
);
ALTER TABLE
    `Pilot` ADD UNIQUE `pilot_code_unique`(`code`);
ALTER TABLE
    `Pilot` ADD CONSTRAINT `pilot_districtid_foreign` FOREIGN KEY(`districtid`) REFERENCES `districts`(`districtid`);
CREATE TABLE `class`(
    `classid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `teacherid` BIGINT UNSIGNED NOT NULL,
    `subject` VARCHAR(255) NOT NULL,
    `name` VARCHAR(255) NOT NULL,
    `type` ENUM('standard', 'special-education', 'excelerated/AP') NOT NULL,
    `gradeLevel` SMALLINT NOT NULL,
    `schoolYear` VARCHAR(9) NOT NULL,
    `createdAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updatedAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE `Students`(
    `studentid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `classid` BIGINT UNSIGNED NOT NULL,
    `fname` VARCHAR(255) NOT NULL,
    `lname` VARCHAR(255) NOT NULL,
    `grade-level` SMALLINT NOT NULL,
    `reading-level` SMALLINT NOT NULL,
    `notes` TEXT NOT NULL,
    `createdAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updatedAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE `vocabList`(
    `vocabListid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `studentid` BIGINT UNSIGNED NOT NULL,
    `listName` VARCHAR(255) NOT NULL,
    `description` VARCHAR(255) NOT NULL,
    `createdAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updatedAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE `worksheets`(
    `sheetid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `teacherid` BIGINT UNSIGNED NOT NULL,
    `studentid` BIGINT UNSIGNED NULL,
    `vocabListid` BIGINT UNSIGNED NULL,
    `title` VARCHAR(255) NOT NULL,
    `createdAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `sheettype` ENUM('passage', 'worksheet') NOT NULL,
    `original` BOOLEAN NOT NULL,
    `informational` BOOLEAN NOT NULL,
    `interest` VARCHAR(255) NOT NULL,
    `phonics` VARCHAR(255) NULL,
    `sourceurl` VARCHAR(255) NULL,
    `expectedLevel` SMALLINT NOT NULL,
    `DOKLevel` ENUM('1', '2', '3', '4', 'custom') NULL,
    `DOKRequest` JSON NULL,
    `studentGradeAtGeneration` SMALLINT NULL,
    CONSTRAINT `worksheets_dokrequest_check` CHECK (
        (`DOKLevel` = 'custom' AND `DOKRequest` IS NOT NULL)
        OR ((`DOKLevel` IS NULL OR `DOKLevel` <> 'custom') AND `DOKRequest` IS NULL)
    )
);
ALTER TABLE
    `worksheets` ADD INDEX `worksheets_teacherid_createdat_index`(`teacherid`, `createdAt`);
CREATE TABLE `words`(
    `wordid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `vocabListid` BIGINT UNSIGNED NULL,
    `sheetid` BIGINT UNSIGNED NULL,
    `word` VARCHAR(255) NOT NULL,
    `createdAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updatedAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT `words_parent_check` CHECK (
        (`vocabListid` IS NOT NULL AND `sheetid` IS NULL)
        OR (`vocabListid` IS NULL AND `sheetid` IS NOT NULL)
    )
);
CREATE TABLE `versions`(
    `versionid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `generationid` CHAR(32) NOT NULL,
    `sheetid` BIGINT UNSIGNED NOT NULL,
    `parentVersionid` BIGINT UNSIGNED NULL,
    `source` ENUM(
        'initial',
        'edit',
        'regen_image',
        'regen_full'
    ) NOT NULL,
    `title` VARCHAR(255) NOT NULL,
    `content` TEXT NOT NULL,
    `trueLevel` SMALLINT NOT NULL,
    `DOKMix` JSON NULL,
    `pdfBlobKey` VARCHAR(255) NULL,
    `imageBlobKey` VARCHAR(255) NULL,
    `rating` SMALLINT NULL,
    `feedback` TEXT NULL,
    `createdAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `costUsd` DECIMAL(10, 6) NULL,
    CONSTRAINT `versions_rating_check` CHECK (`rating` IS NULL OR (`rating` BETWEEN 1 AND 5))
);
ALTER TABLE
    `versions` ADD INDEX `versions_sheetid_createdat_index`(`sheetid`, `createdAt`);
ALTER TABLE
    `versions` ADD UNIQUE `versions_generationid_unique`(`generationid`);
CREATE TABLE `downloads`(
    `downloadid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `versionid` BIGINT UNSIGNED NOT NULL,
    `downloadedAt` DATETIME NOT NULL,
    `format` VARCHAR(16) NOT NULL
);
ALTER TABLE
    `downloads` ADD INDEX `downloads_versionid_downloadedat_index`(`versionid`, `downloadedAt`);
CREATE TABLE `subscriptions`(
    `subid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `teacherid` BIGINT UNSIGNED NOT NULL,
    `pilotid` BIGINT UNSIGNED NULL,
    `stripeCustomerId` VARCHAR(255) NULL,
    `stripeSubscriptionId` VARCHAR(255) NULL,
    `priceId` VARCHAR(255) NULL,
    `price` DECIMAL(10, 2) NOT NULL,
    `status` VARCHAR(32) NOT NULL,
    `currentPeriodEnd` DATETIME NULL,
    `createdAt` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `canceledAt` DATETIME NULL,
    CONSTRAINT `subscriptions_kind_check` CHECK (
        (`pilotid` IS NOT NULL AND `stripeCustomerId` IS NULL AND `stripeSubscriptionId` IS NULL)
        OR (`pilotid` IS NULL AND `stripeCustomerId` IS NOT NULL AND `stripeSubscriptionId` IS NOT NULL)
    )
);
ALTER TABLE
    `subscriptions` ADD UNIQUE `subscriptions_teacherid_pilotid_unique`(`teacherid`, `pilotid`);
ALTER TABLE
    `subscriptions` ADD INDEX `subscriptions_teacherid_createdat_index`(`teacherid`, `createdAt`);
ALTER TABLE
    `subscriptions` ADD UNIQUE `subscriptions_stripesubscriptionid_unique`(`stripeSubscriptionId`);
CREATE TABLE `sessions`(
    `sessionid` CHAR(36) NOT NULL,
    `teacherid` BIGINT UNSIGNED NOT NULL,
    `startedAt` DATETIME NOT NULL,
    `endedAt` DATETIME NULL,
    `deviceType` VARCHAR(32) NULL,
    `browser` VARCHAR(64) NULL,
    `os` VARCHAR(64) NULL,
    `appVersion` VARCHAR(32) NULL,
    PRIMARY KEY(`sessionid`)
);
ALTER TABLE
    `sessions` ADD INDEX `sessions_teacherid_startedat_index`(`teacherid`, `startedAt`);
CREATE TABLE `Events`(
    `eventid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `eventType` VARCHAR(255) NOT NULL,
    `teacherid` BIGINT UNSIGNED NULL,
    `pilotid` BIGINT UNSIGNED NULL,
    `eventTime` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `entity` VARCHAR(255) NULL,
    `entityid` BIGINT UNSIGNED NULL,
    `statusCode` SMALLINT NULL,
    `errorMessage` VARCHAR(500) NULL,
    `success` BOOLEAN NOT NULL,
    `ip` VARCHAR(255) NULL,
    CONSTRAINT `events_entity_pair_check` CHECK ((`entity` IS NULL) = (`entityid` IS NULL)),
    CONSTRAINT `events_success_message_check` CHECK (
        (`success` = 1 AND `errorMessage` IS NULL)
        OR (`success` = 0 AND `errorMessage` IS NOT NULL)
    )
);
ALTER TABLE
    `Events` ADD INDEX `events_eventtype_eventtime_index`(`eventType`, `eventTime`);
ALTER TABLE
    `Events` ADD INDEX `events_teacherid_eventtime_index`(`teacherid`, `eventTime`);
ALTER TABLE
    `Events` ADD INDEX `events_pilotid_eventtime_index`(`pilotid`, `eventTime`);
ALTER TABLE
    `Events` ADD INDEX `events_entity_entityid_index`(`entity`, `entityid`);
ALTER TABLE
    `Events` ADD INDEX `events_success_eventtime_index`(`success`, `eventTime`);
CREATE TABLE `Survey`(
    `surveyid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `surveyDate` DATETIME NOT NULL,
    `title` VARCHAR(255) NOT NULL,
    `description` TEXT NOT NULL,
    `baseline` BOOLEAN NULL,
    `pilotid` BIGINT UNSIGNED NULL
);
CREATE TABLE `Question`(
    `questionid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `content` TEXT NOT NULL,
    `surveyid` BIGINT UNSIGNED NOT NULL,
    `questionType` VARCHAR(255) NOT NULL
);
CREATE TABLE `Answer`(
    `answerid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `content` TEXT NOT NULL,
    `teacherid` BIGINT UNSIGNED NOT NULL,
    `questionid` BIGINT UNSIGNED NOT NULL,
    `answertime` DATETIME NOT NULL
);
ALTER TABLE
    `words` ADD CONSTRAINT `words_sheetid_foreign` FOREIGN KEY(`sheetid`) REFERENCES `worksheets`(`sheetid`);
ALTER TABLE
    `worksheets` ADD CONSTRAINT `worksheets_vocablistid_foreign` FOREIGN KEY(`vocabListid`) REFERENCES `vocabList`(`vocabListid`);
ALTER TABLE
    `class` ADD CONSTRAINT `class_teacherid_foreign` FOREIGN KEY(`teacherid`) REFERENCES `Teachers`(`teacherid`);
ALTER TABLE
    `subscriptions` ADD CONSTRAINT `subscriptions_pilotid_foreign` FOREIGN KEY(`pilotid`) REFERENCES `Pilot`(`pilotid`);
ALTER TABLE
    `versions` ADD CONSTRAINT `versions_parentversionid_foreign` FOREIGN KEY(`parentVersionid`) REFERENCES `versions`(`versionid`);
ALTER TABLE
    `worksheets` ADD CONSTRAINT `worksheets_teacherid_foreign` FOREIGN KEY(`teacherid`) REFERENCES `Teachers`(`teacherid`);
ALTER TABLE
    `Answer` ADD CONSTRAINT `answer_teacherid_foreign` FOREIGN KEY(`teacherid`) REFERENCES `Teachers`(`teacherid`);
ALTER TABLE
    `words` ADD CONSTRAINT `words_vocablistid_foreign` FOREIGN KEY(`vocabListid`) REFERENCES `vocabList`(`vocabListid`);
ALTER TABLE
    `vocabList` ADD CONSTRAINT `vocablist_studentid_foreign` FOREIGN KEY(`studentid`) REFERENCES `Students`(`studentid`);
ALTER TABLE
    `worksheets` ADD CONSTRAINT `worksheets_studentid_foreign` FOREIGN KEY(`studentid`) REFERENCES `Students`(`studentid`);
ALTER TABLE
    `Answer` ADD CONSTRAINT `answer_questionid_foreign` FOREIGN KEY(`questionid`) REFERENCES `Question`(`questionid`);
ALTER TABLE
    `subscriptions` ADD CONSTRAINT `subscriptions_teacherid_foreign` FOREIGN KEY(`teacherid`) REFERENCES `Teachers`(`teacherid`);
ALTER TABLE
    `versions` ADD CONSTRAINT `versions_sheetid_foreign` FOREIGN KEY(`sheetid`) REFERENCES `worksheets`(`sheetid`);
ALTER TABLE
    `Survey` ADD CONSTRAINT `survey_pilotid_foreign` FOREIGN KEY(`pilotid`) REFERENCES `Pilot`(`pilotid`) ON DELETE SET NULL;
ALTER TABLE
    `Events` ADD CONSTRAINT `events_pilotid_foreign` FOREIGN KEY(`pilotid`) REFERENCES `Pilot`(`pilotid`) ON DELETE SET NULL;
ALTER TABLE
    `Question` ADD CONSTRAINT `question_surveyid_foreign` FOREIGN KEY(`surveyid`) REFERENCES `Survey`(`surveyid`);
ALTER TABLE
    `Events` ADD CONSTRAINT `events_teacherid_foreign` FOREIGN KEY(`teacherid`) REFERENCES `Teachers`(`teacherid`) ON DELETE SET NULL;
ALTER TABLE
    `downloads` ADD CONSTRAINT `downloads_versionid_foreign` FOREIGN KEY(`versionid`) REFERENCES `versions`(`versionid`);
ALTER TABLE
    `sessions` ADD CONSTRAINT `sessions_teacherid_foreign` FOREIGN KEY(`teacherid`) REFERENCES `Teachers`(`teacherid`);
ALTER TABLE
    `Students` ADD CONSTRAINT `students_classid_foreign` FOREIGN KEY(`classid`) REFERENCES `class`(`classid`);