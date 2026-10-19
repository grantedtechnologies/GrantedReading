-- DEV ONLY. Do not run this file in production.
-- Passwords are plaintext password123 on purpose for local and Railway dummy data.
-- Worksheets, versions, and downloads are not seeded. Those rows belong to PDFs
-- that were actually stored.
-- Sessions and Events use the same timestamps as the rows above.

INSERT INTO `Teachers` (fname, lname, email, password, verified, createdAt, country, state, city, school, district) VALUES
  ('Maya', 'Chen', 'maya.chen@granted.local', 'password123', 1, '2026-06-01 09:00:00', 'United States', 'New York', 'New York', 'Lincoln Elementary', 'Metro District'),
  ('Luis', 'Rivera', 'luis.rivera@granted.local', 'password123', 1, '2026-09-01 09:00:00', 'United States', 'California', 'Riverside', 'Grant Middle', 'Metro District'),
  ('Priya', 'Nair', 'priya.nair@granted.local', 'password123', 1, '2026-08-15 09:00:00', 'United States', 'California', 'Riverside', 'Riverside Elementary', 'Riverside Unified'),
  ('Sam', 'Ortiz', 'sam.ortiz@granted.local', 'password123', 1, '2026-01-20 09:00:00', 'United States', 'California', 'Riverside', 'Grant Middle', 'Metro District');

-- Both seed pilots are limited to Riverside Unified. District rows load before this file.
INSERT INTO `Pilot` (name, school, code, startTime, endTime, createdAt, districtid)
SELECT 'Riverside USD 2026', 'Riverside Unified', 'RIVERSIDE2026', '2026-08-01 00:00:00', '2027-06-30 23:59:59', '2026-08-01 09:00:00', districtid
FROM districts
WHERE name = 'Riverside Unified' AND state = 'California'
LIMIT 1;
INSERT INTO `Pilot` (name, school, code, startTime, endTime, createdAt, districtid)
SELECT 'Spring pilot 2026', 'Grant Middle', 'SPRING2026', '2026-01-15 00:00:00', '2026-10-20 23:59:59', '2026-01-10 09:00:00', districtid
FROM districts
WHERE name = 'Riverside Unified' AND state = 'California'
LIMIT 1;

INSERT INTO `class` (teacherid, subject, name, `type`, gradeLevel, schoolYear, createdAt, updatedAt) VALUES
  (1, 'English', '1st Period ELA', 'standard', 4, '2026-2027', '2026-09-28 15:00:00', '2026-09-28 15:00:00'),
  (1, 'Science', '2nd Period Science', 'special-education', 4, '2026-2027', '2026-09-28 15:10:00', '2026-09-28 15:10:00'),
  (2, 'History', '3rd Period History', 'standard', 6, '2026-2027', '2026-09-29 16:30:00', '2026-09-29 16:30:00'),
  (2, 'Reading', 'Reading Lab', 'excelerated/AP', 6, '2026-2027', '2026-09-29 16:40:00', '2026-09-29 16:40:00'),
  (3, 'English', 'Pilot ELA', 'standard', 5, '2026-2027', '2026-09-30 10:30:00', '2026-09-30 10:30:00'),
  (4, 'Reading', 'Expired Pilot Lab', 'standard', 3, '2025-2026', '2026-10-03 13:00:00', '2026-10-03 13:00:00');

INSERT INTO `Students` (classid, fname, lname, `grade-level`, `reading-level`, notes, createdAt, updatedAt) VALUES
  (1, 'Jordan', 'Hale', 4, 2, 'Works hard with short decodable stories. Loves dinosaurs.', '2026-09-28 15:20:00', '2026-09-28 15:20:00'),
  (2, 'Sam', 'Lee', 4, 3, 'Strong oral language. Soccer is the reliable hook.', '2026-09-28 15:30:00', '2026-09-28 15:30:00'),
  (3, 'Avery', 'Kim', 6, 4, 'Ready for informational passages if the words are previewed.', '2026-09-29 16:50:00', '2026-09-29 16:50:00'),
  (4, 'Noah', 'Brooks', 6, 3, 'Needs extra passes on long-vowel patterns.', '2026-09-29 17:00:00', '2026-09-29 17:00:00'),
  (5, 'Leela', 'Shah', 5, 4, 'Pilot student. Likes space stories.', '2026-09-30 10:45:00', '2026-09-30 10:45:00'),
  (6, 'Chris', 'Ng', 3, 2, 'Was in the spring pilot.', '2026-10-03 13:15:00', '2026-10-03 13:15:00');

INSERT INTO `vocabList` (studentid, listName, description, createdAt, updatedAt) VALUES
  (1, 'Short a family', 'Decodables for this week', '2026-09-28 16:00:00', '2026-09-29 09:30:00'),
  (1, 'Science week', 'Words from the dinosaur unit', '2026-09-28 16:10:00', '2026-09-28 16:10:00'),
  (2, 'Soccer words', 'Interest words to keep in stories', '2026-09-28 16:20:00', '2026-09-28 16:20:00'),
  (3, 'History unit', 'Preview words before the colonies passage', '2026-09-29 17:10:00', '2026-09-29 17:10:00'),
  (4, 'Long vowels', 'oa, ai, and ee practice', '2026-09-30 11:00:00', '2026-09-30 11:00:00'),
  (4, 'Story words', 'Comprehension vocabulary', '2026-10-03 13:30:00', '2026-10-03 13:30:00');

INSERT INTO `words` (vocabListid, word, createdAt, updatedAt) VALUES
  (1, 'cat', '2026-09-28 16:00:00', '2026-09-28 16:00:00'),
  (1, 'hat', '2026-09-28 16:00:00', '2026-09-28 16:00:00'),
  (1, 'map', '2026-09-28 16:00:00', '2026-09-28 16:00:00'),
  (2, 'fossil', '2026-09-28 16:10:00', '2026-09-28 16:10:00'),
  (2, 'habitat', '2026-09-28 16:10:00', '2026-09-28 16:10:00'),
  (2, 'action', '2026-09-28 16:10:00', '2026-09-28 16:10:00'),
  (3, 'goal', '2026-09-28 16:20:00', '2026-09-28 16:20:00'),
  (3, 'pass', '2026-09-28 16:20:00', '2026-09-28 16:20:00'),
  (3, 'teammate', '2026-09-28 16:20:00', '2026-09-28 16:20:00'),
  (4, 'colony', '2026-09-29 17:10:00', '2026-09-29 17:10:00'),
  (4, 'settle', '2026-09-29 17:10:00', '2026-09-29 17:10:00'),
  (4, 'trade', '2026-09-29 17:10:00', '2026-09-29 17:10:00'),
  (5, 'boat', '2026-09-30 11:00:00', '2026-09-30 11:00:00'),
  (5, 'rain', '2026-09-30 11:00:00', '2026-09-30 11:00:00'),
  (5, 'seed', '2026-09-30 11:00:00', '2026-09-30 11:00:00'),
  (6, 'character', '2026-10-03 13:30:00', '2026-10-03 13:30:00'),
  (6, 'setting', '2026-10-03 13:30:00', '2026-10-03 13:30:00'),
  (6, 'problem', '2026-10-03 13:30:00', '2026-10-03 13:30:00');

-- Maya is Standard (canceled Stripe only). Luis is paid Pro.
-- Priya is on Riverside through June 2027. Sam is on the spring pilot through October 20.
-- Access is derived at read time.
INSERT INTO `subscriptions`
  (teacherid, pilotid, stripeCustomerId, stripeSubscriptionId, priceId, price, status, currentPeriodEnd, createdAt, canceledAt)
VALUES
  (1, NULL, 'cus_MayaChen8Q2k', 'sub_1P6cMayaChen04', 'price_dev_pro', 5.99, 'canceled', '2026-08-01 00:00:00', '2026-07-01 00:00:00', '2026-08-01 00:00:00'),
  (2, NULL, 'cus_dev_luis', 'sub_dev_luis', 'price_dev_pro', 5.99, 'active', '2026-10-28 00:00:00', '2026-09-28 00:00:00', NULL),
  (3, 1, NULL, NULL, NULL, 0.00, 'active', NULL, '2026-09-01 00:00:00', NULL),
  (4, 2, NULL, NULL, NULL, 0.00, 'active', NULL, '2026-02-01 00:00:00', NULL);

INSERT INTO sessions (sessionid, teacherid, startedAt, endedAt, deviceType, browser, os, appVersion) VALUES
  ('10000000-0000-4000-8000-000000000001', 1, '2026-09-28 14:02:00', '2026-09-28 16:40:00', 'desktop', 'Chrome', 'macOS', 'seed'),
  ('10000000-0000-4000-8000-000000000002', 2, '2026-09-29 16:05:00', '2026-09-29 17:20:00', 'desktop', 'Chrome', 'macOS', 'seed'),
  ('10000000-0000-4000-8000-000000000003', 3, '2026-09-30 10:00:00', '2026-09-30 11:15:00', 'desktop', 'Chrome', 'macOS', 'seed'),
  ('10000000-0000-4000-8000-000000000004', 1, '2026-10-01 15:10:00', '2026-10-01 16:00:00', 'desktop', 'Chrome', 'macOS', 'seed'),
  ('10000000-0000-4000-8000-000000000005', 2, '2026-10-02 11:20:00', '2026-10-02 12:05:00', 'desktop', 'Chrome', 'macOS', 'seed'),
  ('10000000-0000-4000-8000-000000000006', 4, '2026-10-03 12:30:00', '2026-10-03 13:40:00', 'desktop', 'Chrome', 'macOS', 'seed'),
  ('10000000-0000-4000-8000-000000000007', 3, '2026-10-05 18:45:00', NULL, 'desktop', 'Chrome', 'macOS', 'seed'),
  ('10000000-0000-4000-8000-000000000008', 1, '2026-10-06 13:40:00', NULL, 'desktop', 'Chrome', 'macOS', 'seed'),
  ('10000000-0000-4000-8000-000000000009', 2, '2026-10-07 09:15:00', NULL, 'desktop', 'Chrome', 'macOS', 'seed'),
  ('10000000-0000-4000-8000-000000000010', 4, '2026-10-08 08:05:00', NULL, 'desktop', 'Chrome', 'macOS', 'seed');

INSERT INTO `Events` (eventType, teacherid, pilotid, eventTime, entity, entityid, statusCode, errorMessage, success) VALUES
  ('login', 1, NULL, '2026-09-28 14:02:00', NULL, NULL, 200, NULL, 1),
  ('login', 2, NULL, '2026-09-29 16:05:00', NULL, NULL, 200, NULL, 1),
  ('login', 3, 1, '2026-09-30 10:00:00', NULL, NULL, 200, NULL, 1),
  ('login', 1, NULL, '2026-10-01 15:10:00', NULL, NULL, 200, NULL, 1),
  ('login', 2, NULL, '2026-10-02 11:20:00', NULL, NULL, 200, NULL, 1),
  ('login', 4, 2, '2026-10-03 12:30:00', NULL, NULL, 200, NULL, 1),
  ('login', 3, 1, '2026-10-05 18:45:00', NULL, NULL, 200, NULL, 1),
  ('login', 1, NULL, '2026-10-06 13:40:00', NULL, NULL, 200, NULL, 1),
  ('login', 2, NULL, '2026-10-07 09:15:00', NULL, NULL, 200, NULL, 1),
  ('login', 4, 2, '2026-10-08 08:05:00', NULL, NULL, 200, NULL, 1),
  ('class_created', 1, NULL, '2026-09-28 15:00:00', 'class', 1, 200, NULL, 1),
  ('class_created', 1, NULL, '2026-09-28 15:10:00', 'class', 2, 200, NULL, 1),
  ('class_created', 2, NULL, '2026-09-29 16:30:00', 'class', 3, 200, NULL, 1),
  ('class_created', 2, NULL, '2026-09-29 16:40:00', 'class', 4, 200, NULL, 1),
  ('class_created', 3, 1, '2026-09-30 10:30:00', 'class', 5, 200, NULL, 1),
  ('class_created', 4, 2, '2026-10-03 13:00:00', 'class', 6, 200, NULL, 1),
  ('student_created', 1, NULL, '2026-09-28 15:20:00', 'student', 1, 200, NULL, 1),
  ('student_created', 1, NULL, '2026-09-28 15:30:00', 'student', 2, 200, NULL, 1),
  ('student_created', 2, NULL, '2026-09-29 16:50:00', 'student', 3, 200, NULL, 1),
  ('student_created', 2, NULL, '2026-09-29 17:00:00', 'student', 4, 200, NULL, 1),
  ('student_created', 3, 1, '2026-09-30 10:45:00', 'student', 5, 200, NULL, 1),
  ('student_created', 4, 2, '2026-10-03 13:15:00', 'student', 6, 200, NULL, 1),
  ('vocab_list_created', 1, NULL, '2026-09-28 16:00:00', 'vocabList', 1, 200, NULL, 1),
  ('vocab_list_created', 1, NULL, '2026-09-28 16:10:00', 'vocabList', 2, 200, NULL, 1),
  ('vocab_list_created', 1, NULL, '2026-09-28 16:20:00', 'vocabList', 3, 200, NULL, 1),
  ('vocab_list_created', 2, NULL, '2026-09-29 17:10:00', 'vocabList', 4, 200, NULL, 1),
  ('vocab_list_created', 2, NULL, '2026-09-30 11:00:00', 'vocabList', 5, 200, NULL, 1),
  ('vocab_list_created', 2, NULL, '2026-10-03 13:30:00', 'vocabList', 6, 200, NULL, 1),
  ('login', NULL, NULL, '2026-10-09 08:12:00', NULL, NULL, 401, 'unknown_email', 0),
  ('login', 1, NULL, '2026-10-06 13:38:00', NULL, NULL, 401, 'invalid_password', 0),
  ('logout', 1, NULL, '2026-10-01 16:00:00', NULL, NULL, 200, NULL, 1),
  ('logout', 2, NULL, '2026-10-02 12:05:00', NULL, NULL, 200, NULL, 1),
  ('vocab_list_updated', 1, NULL, '2026-09-29 09:30:00', 'vocabList', 1, 200, NULL, 1),
  ('vocab_list_deleted', 1, NULL, '2026-10-01 10:00:00', 'vocabList', 7, 200, NULL, 1),
  ('vocab_list_deleted', 1, NULL, '2026-10-04 11:00:00', 'vocabList', 4, 404, 'not_found', 0),
  ('worksheet_generated', 1, NULL, '2026-10-06 14:10:00', NULL, NULL, 429, 'quota_exceeded', 0);
