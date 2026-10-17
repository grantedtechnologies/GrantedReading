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
DROP TABLE IF EXISTS Events;
DROP TABLE IF EXISTS Survey;
DROP TABLE IF EXISTS Question;
DROP TABLE IF EXISTS Answer;
DROP TABLE IF EXISTS Pilot;
DROP TABLE IF EXISTS vocabList;
DROP TABLE IF EXISTS vocab_lists;
DROP TABLE IF EXISTS Students;
DROP TABLE IF EXISTS class;
DROP TABLE IF EXISTS Teachers;

CREATE TABLE `Teachers`(
    `teacherid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `fname` VARCHAR(255) NOT NULL,
    `lname` VARCHAR(255) NOT NULL,
    `email` VARCHAR(255) NOT NULL,
    `password` VARCHAR(255) NOT NULL,
    `verified` BOOLEAN NOT NULL,
    `createdAt` DATETIME NOT NULL
);
ALTER TABLE
    `Teachers` ADD INDEX `teachers_email_index`(`email`);
CREATE TABLE `Pilot`(
    `pilotid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `name` VARCHAR(255) NOT NULL, 
    `school` VARCHAR(255) NOT NULL,
    `code` VARCHAR(255) NOT NULL,
    `startTime` DATETIME NOT NULL,
    `endTime` DATETIME NOT NULL,
    `createdAt` DATETIME NOT NULL
);
ALTER TABLE
    `Pilot` ADD UNIQUE `pilot_code_unique`(`code`);
CREATE TABLE `class`(
    `classid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `teacherid` BIGINT UNSIGNED NOT NULL,
    `subject` VARCHAR(255) NOT NULL,
    `name` VARCHAR(255) NOT NULL,
    `gradeLevel` SMALLINT NOT NULL,
    `schoolYear` VARCHAR(9) NOT NULL,
    `createdAt` DATETIME NOT NULL,
    `updatedAt` DATETIME NOT NULL
);
CREATE TABLE `Students`(
    `studentid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `classid` BIGINT UNSIGNED NOT NULL,
    `fname` VARCHAR(255) NOT NULL,
    `lname` VARCHAR(255) NOT NULL,
    `grade-level` SMALLINT NOT NULL,
    `reading-level` SMALLINT NOT NULL,
    `notes` TEXT NOT NULL,
    `createdAt` DATETIME NOT NULL,
    `updatedAt` DATETIME NOT NULL
);
CREATE TABLE `vocabList`(
    `vocabListid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `studentid` BIGINT UNSIGNED NOT NULL,
    `listName` VARCHAR(255) NOT NULL,
    `description` VARCHAR(255) NOT NULL,
    `createdAt` DATETIME NOT NULL,
    `updatedAt` DATETIME NOT NULL
);
CREATE TABLE `worksheets`(
    `sheetid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `teacherid` BIGINT UNSIGNED NOT NULL,
    `studentid` BIGINT UNSIGNED NULL,
    `vocabListid` BIGINT UNSIGNED NULL,
    `title` VARCHAR(255) NOT NULL,
    `createdAt` DATETIME NOT NULL,
    `sheettype` ENUM('passage', 'worksheet') NOT NULL,
    `original` BOOLEAN NOT NULL,
    `informational` BOOLEAN NOT NULL,
    `interest` VARCHAR(255) NOT NULL,
    `phonics` VARCHAR(255) NULL,
    `sourceurl` VARCHAR(255) NULL,
    `expectedLevel` SMALLINT NOT NULL,
    `DOKLevel` ENUM('1', '2', '3', '4', 'custom') NULL,
    `DOKRequest` JSON NULL,
    `studentGradeAtGeneration` SMALLINT NULL
);
ALTER TABLE
    `worksheets` ADD INDEX `worksheets_teacherid_createdat_index`(`teacherid`, `createdAt`);
CREATE TABLE `words`(
    `wordid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `vocabListid` BIGINT UNSIGNED NULL,
    `sheetid` BIGINT UNSIGNED NULL,
    `word` VARCHAR(255) NOT NULL,
    `createdAt` DATETIME NOT NULL,
    `updatedAt` DATETIME NOT NULL
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
    `createdAt` DATETIME NOT NULL,
    `costUsd` DECIMAL(10, 6) NULL
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
    `createdAt` DATETIME NOT NULL,
    `canceledAt` DATETIME NULL
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
    `teacherid` BIGINT NULL,
    `eventTime` DATETIME NOT NULL,
    `entityid` BIGINT NULL,
    `entity` VARCHAR(255) NULL,
    `statusCode` VARCHAR(255) NOT NULL,
    `errorMessaage` VARCHAR(255) NOT NULL,
    `success` BOOLEAN NOT NULL,
    `ip` VARCHAR(255) NOT NULL,
    `pilotid` BIGINT NULL
);
CREATE TABLE `Survey`(
    `surveyid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `surveyDate` DATETIME NOT NULL,
    `title` VARCHAR(255) NOT NULL,
    `description` TEXT NOT NULL,
    `baseline` BOOLEAN NULL,
    `pilotid` BIGINT NULL
);
CREATE TABLE `Question`(
    `questionid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `content` TEXT NOT NULL,
    `surveyid` BIGINT NOT NULL,
    `questionType` VARCHAR(255) NOT NULL
);
CREATE TABLE `Answer`(
    `answerid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `content` TEXT NOT NULL,
    `teacherid` BIGINT NOT NULL,
    `questionid` BIGINT NOT NULL,
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
    `Survey` ADD CONSTRAINT `survey_pilotid_foreign` FOREIGN KEY(`pilotid`) REFERENCES `Pilot`(`pilotid`);
ALTER TABLE
    `Events` ADD CONSTRAINT `events_pilotid_foreign` FOREIGN KEY(`pilotid`) REFERENCES `Pilot`(`pilotid`);
ALTER TABLE
    `Question` ADD CONSTRAINT `question_surveyid_foreign` FOREIGN KEY(`surveyid`) REFERENCES `Survey`(`surveyid`);
ALTER TABLE
    `Events` ADD CONSTRAINT `events_teacherid_foreign` FOREIGN KEY(`teacherid`) REFERENCES `Teachers`(`teacherid`);
ALTER TABLE
    `downloads` ADD CONSTRAINT `downloads_versionid_foreign` FOREIGN KEY(`versionid`) REFERENCES `versions`(`versionid`);
ALTER TABLE
    `sessions` ADD CONSTRAINT `sessions_teacherid_foreign` FOREIGN KEY(`teacherid`) REFERENCES `Teachers`(`teacherid`);
ALTER TABLE
    `Students` ADD CONSTRAINT `students_classid_foreign` FOREIGN KEY(`classid`) REFERENCES `class`(`classid`);