DROP DATABASE IF EXISTS granteddb;
CREATE DATABASE granteddb
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE granteddb;

DROP TABLE IF EXISTS words;
DROP TABLE IF EXISTS vocab_lists;
DROP TABLE IF EXISTS students;
DROP TABLE IF EXISTS teachers;

CREATE TABLE teachers (
  teacher_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  first_name VARCHAR(255) NOT NULL,
  last_name VARCHAR(255) NOT NULL,
  email VARCHAR(255) NOT NULL,
  password VARCHAR(255) NOT NULL,
  membership ENUM('standard', 'pro') NOT NULL DEFAULT 'standard',
  PRIMARY KEY (teacher_id),
  UNIQUE KEY teachers_email_unique (email)
);

CREATE TABLE students (
  student_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  teacher_id BIGINT UNSIGNED NOT NULL,
  first_name VARCHAR(255) NOT NULL,
  last_name VARCHAR(255) NOT NULL,
  classroom_grade SMALLINT NOT NULL,
  reading_level SMALLINT NOT NULL,
  notes TEXT NULL,
  PRIMARY KEY (student_id),
  CONSTRAINT students_teacher_id_foreign
    FOREIGN KEY (teacher_id) REFERENCES teachers (teacher_id)
    ON DELETE CASCADE
);

CREATE TABLE vocab_lists (
  vocab_list_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  student_id BIGINT UNSIGNED NOT NULL,
  list_name VARCHAR(255) NOT NULL,
  description VARCHAR(255) NULL,
  PRIMARY KEY (vocab_list_id),
  CONSTRAINT vocab_lists_student_id_foreign
    FOREIGN KEY (student_id) REFERENCES students (student_id)
    ON DELETE CASCADE
);

CREATE TABLE words (
  word_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  vocab_list_id BIGINT UNSIGNED NOT NULL,
  word VARCHAR(255) NOT NULL,
  PRIMARY KEY (word_id),
  CONSTRAINT words_vocab_list_id_foreign
    FOREIGN KEY (vocab_list_id) REFERENCES vocab_lists (vocab_list_id)
    ON DELETE CASCADE
);

-- Dummy passwords are plaintext on purpose. Hash them when login is wired up.
INSERT INTO teachers (first_name, last_name, email, password, membership) VALUES
  ('Maya', 'Chen', 'maya.chen@granted.local', 'password123', 'standard'),
  ('Luis', 'Rivera', 'luis.rivera@granted.local', 'password123', 'pro');

INSERT INTO students (teacher_id, first_name, last_name, classroom_grade, reading_level, notes) VALUES
  (1, 'Jordan', 'Hale', 4, 2, 'Works hard with short decodable stories. Loves dinosaurs.'),
  (1, 'Sam', 'Ortiz', 4, 3, 'Strong oral language. Soccer is the reliable hook.'),
  (2, 'Avery', 'Kim', 6, 4, 'Ready for informational passages if the words are previewed.'),
  (2, 'Noah', 'Brooks', 6, 3, 'Needs extra passes on long-vowel patterns.');

INSERT INTO vocab_lists (student_id, list_name, description) VALUES
  (1, 'Short a family', 'Decodables for this week'),
  (1, 'Science week', 'Words from the dinosaur unit'),
  (2, 'Soccer words', 'Interest words to keep in stories'),
  (3, 'History unit', 'Preview words before the colonies passage'),
  (4, 'Long vowels', 'oa, ai, and ee practice'),
  (4, 'Story words', 'Comprehension vocabulary');

INSERT INTO words (vocab_list_id, word) VALUES
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
