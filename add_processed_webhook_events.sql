-- Safe to run on an existing database. Does not drop or rewrite other tables.
-- Records each Stripe webhook id once so a retry is not applied twice.

CREATE TABLE IF NOT EXISTS `processed_webhook_events`(
    `eventId` VARCHAR(255) NOT NULL,
    `eventType` VARCHAR(255) NOT NULL,
    `processedAt` DATETIME NOT NULL,
    PRIMARY KEY(`eventId`)
);
