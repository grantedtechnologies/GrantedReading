DROP DATABASE IF EXISTS granteddb;
CREATE DATABASE granteddb
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE granteddb;

DROP TABLE IF EXISTS subscriptions;
DROP TABLE IF EXISTS words;
DROP TABLE IF EXISTS worksheets;
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
    `verified` BOOLEAN NOT NULL
);
CREATE TABLE `Students`(
    `studentid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `classid` BIGINT UNSIGNED NOT NULL,
    `fname` VARCHAR(255) NOT NULL,
    `lname` VARCHAR(255) NOT NULL,
    `grade-level` SMALLINT NOT NULL,
    `roading-level` SMALLINT NOT NULL,
    `notes` TEXT NOT NULL
);
CREATE TABLE `vocabList`(
    `vocabListid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `studentid` BIGINT UNSIGNED NOT NULL,
    `listName` VARCHAR(255) NOT NULL,
    `description` VARCHAR(255) NOT NULL
);
CREATE TABLE `words`(
    `wordid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `vocabListid` BIGINT UNSIGNED NULL,
    `word` VARCHAR(255) NOT NULL,
    `sheetid` BIGINT UNSIGNED NULL
);
CREATE TABLE `worksheets`(
    `sheetid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `generationid` CHAR(32) NULL,
    `title` VARCHAR(255) NOT NULL,
    `createdAt` DATETIME NOT NULL,
    `expectedLevel` SMALLINT NOT NULL,
    `DOKLevel` SMALLINT NULL,
    `sheettype` ENUM('passage', 'worksheet') NOT NULL,
    `phonics` VARCHAR(255) NULL,
    `content` TEXT NULL,
    `informational` BOOLEAN NOT NULL,
    `interest` VARCHAR(255) NOT NULL,
    `vocabListid` BIGINT UNSIGNED NULL,
    `sourceurl` VARCHAR(255) NULL,
    `bloburl` VARCHAR(255) NOT NULL,
    `studentid` BIGINT UNSIGNED NULL,
    `feedback` TEXT NULL,
    `rating` SMALLINT NULL,
    `original` BOOLEAN NOT NULL,
    `trueLevel` SMALLINT NOT NULL,
    `teacherid` BIGINT UNSIGNED NOT NULL,
    `DOKMix` JSON NULL
);
ALTER TABLE
    `worksheets` ADD UNIQUE `worksheets_generationid_unique`(`generationid`);
CREATE TABLE `subscriptions`(
    `subid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `teacherid` BIGINT UNSIGNED NOT NULL,
    `stripeCustomerId` VARCHAR(255) NOT NULL,
    `stripeSubscriptionId` VARCHAR(255) NOT NULL,
    `price` DECIMAL(10, 2) NOT NULL,
    `status` VARCHAR(32) NOT NULL,
    `currentPeriodEnd` DATETIME NULL,
    `createdAt` DATETIME NOT NULL,
    `canceledAt` DATETIME NULL
);
ALTER TABLE
    `subscriptions` ADD INDEX `subscriptions_teacherid_createdat_index`(`teacherid`, `createdAt`);
ALTER TABLE
    `subscriptions` ADD UNIQUE `subscriptions_stripesubscriptionid_unique`(`stripeSubscriptionId`);
CREATE TABLE `class`(
    `classid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `teacherid` BIGINT UNSIGNED NOT NULL,
    `subject` VARCHAR(255) NOT NULL,
    `name` VARCHAR(255) NOT NULL,
    `gradeLevel` SMALLINT NOT NULL,
    `schoolYear` VARCHAR(4) NOT NULL
);
ALTER TABLE
    `worksheets` ADD CONSTRAINT `worksheets_teacherid_foreign` FOREIGN KEY(`teacherid`) REFERENCES `Teachers`(`teacherid`);
ALTER TABLE
    `class` ADD CONSTRAINT `class_teacherid_foreign` FOREIGN KEY(`teacherid`) REFERENCES `Teachers`(`teacherid`);
ALTER TABLE
    `worksheets` ADD CONSTRAINT `worksheets_studentid_foreign` FOREIGN KEY(`studentid`) REFERENCES `Students`(`studentid`);
ALTER TABLE
    `subscriptions` ADD CONSTRAINT `subscriptions_teacherid_foreign` FOREIGN KEY(`teacherid`) REFERENCES `Teachers`(`teacherid`);
ALTER TABLE
    `vocabList` ADD CONSTRAINT `vocablist_studentid_foreign` FOREIGN KEY(`studentid`) REFERENCES `Students`(`studentid`);
ALTER TABLE
    `words` ADD CONSTRAINT `words_vocablistid_foreign` FOREIGN KEY(`vocabListid`) REFERENCES `vocabList`(`vocabListid`);
ALTER TABLE
    `words` ADD CONSTRAINT `words_sheetid_foreign` FOREIGN KEY(`sheetid`) REFERENCES `worksheets`(`sheetid`);
ALTER TABLE
    `Students` ADD CONSTRAINT `students_classid_foreign` FOREIGN KEY(`classid`) REFERENCES `class`(`classid`);
ALTER TABLE
    `worksheets` ADD CONSTRAINT `worksheets_vocablistid_foreign` FOREIGN KEY(`vocabListid`) REFERENCES `vocabList`(`vocabListid`);
-- Dummy passwords are plaintext on purpose. Hash them when login is wired up.
INSERT INTO `Teachers` (fname, lname, email, password, verified) VALUES
  ('Maya', 'Chen', 'maya.chen@granted.local', 'password123', 1),
  ('Luis', 'Rivera', 'luis.rivera@granted.local', 'password123', 1);

INSERT INTO `class` (teacherid, subject, name, gradeLevel, schoolYear) VALUES
  (1, 'English', '1st Period ELA', 4, '2026'),
  (1, 'Science', '2nd Period Science', 4, '2026'),
  (2, 'History', '3rd Period History', 6, '2026'),
  (2, 'Reading', 'Reading Lab', 6, '2026');

INSERT INTO `Students` (classid, fname, lname, `grade-level`, `roading-level`, notes) VALUES
  (1, 'Jordan', 'Hale', 4, 2, 'Works hard with short decodable stories. Loves dinosaurs.'),
  (2, 'Sam', 'Ortiz', 4, 3, 'Strong oral language. Soccer is the reliable hook.'),
  (3, 'Avery', 'Kim', 6, 4, 'Ready for informational passages if the words are previewed.'),
  (4, 'Noah', 'Brooks', 6, 3, 'Needs extra passes on long-vowel patterns.');

INSERT INTO `vocabList` (studentid, listName, description) VALUES
  (1, 'Short a family', 'Decodables for this week'),
  (1, 'Science week', 'Words from the dinosaur unit'),
  (2, 'Soccer words', 'Interest words to keep in stories'),
  (3, 'History unit', 'Preview words before the colonies passage'),
  (4, 'Long vowels', 'oa, ai, and ee practice'),
  (4, 'Story words', 'Comprehension vocabulary');

INSERT INTO `words` (vocabListid, word) VALUES
  (1, 'cat'),
  (1, 'hat'),
  (1, 'map'),
  (2, 'fossil'),
  (2, 'habitat'),
  (2, 'action'),
  (3, 'goal'),
  (3, 'pass'),
  (3, 'teammate'),
  (4, 'colony'),
  (4, 'settle'),
  (4, 'trade'),
  (5, 'boat'),
  (5, 'rain'),
  (5, 'seed'),
  (6, 'character'),
  (6, 'setting'),
  (6, 'problem');

-- Maya's latest row is canceled, so she is Standard. Luis's latest row is
-- active, so he is Pro. An older canceled row for Luis stays in the history.
INSERT INTO `subscriptions`
  (teacherid, stripeCustomerId, stripeSubscriptionId, price, status, currentPeriodEnd, createdAt, canceledAt)
VALUES
  (1, 'cus_MayaChen8Q2k', 'sub_1P6cMayaChen04', 5.99, 'canceled', '2026-08-01 00:00:00', '2026-07-01 00:00:00', '2026-08-01 00:00:00'),
  (2, 'cus_LuisRivera3N7', 'sub_1N2aLuisRivera09', 5.99, 'canceled', '2026-06-01 00:00:00', '2026-05-01 00:00:00', '2026-06-01 00:00:00'),
  (2, 'cus_LuisRivera3N7', 'sub_1R8dLuisRivera22', 5.99, 'active', '2026-10-28 00:00:00', '2026-09-28 00:00:00', NULL);
