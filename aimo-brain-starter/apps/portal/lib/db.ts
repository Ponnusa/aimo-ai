// Portal's own tiny database (M6): sessions and the portal's own audit
// log of actions taken through it. Nothing else — organization, agent,
// negotiation, approval and order records stay authoritative in
// network-api/registry-api/buyer-agent/supplier-agent, fetched live
// (lib/networkApi.ts, lib/managementApi.ts). See docs/ARCHITECTURE.md's
// M6 section.
//
// Uses Node's built-in node:sqlite (stable enough for this reference
// deployment's single-instance session store; a multi-instance
// deployment would point this at a shared Postgres database behind the
// same two functions below instead).
import { DatabaseSync } from "node:sqlite";
import { config } from "./config";

let db: DatabaseSync | null = null;

export function getDb(): DatabaseSync {
  if (db) return db;
  db = new DatabaseSync(config.sqlitePath);
  db.exec(`
    CREATE TABLE IF NOT EXISTS sessions (
      id TEXT PRIMARY KEY,
      organization_id TEXT NOT NULL,
      oidc_subject TEXT NOT NULL,
      email TEXT NOT NULL,
      display_name TEXT NOT NULL,
      created_at TEXT NOT NULL,
      reauthenticated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      organization_id TEXT NOT NULL,
      oidc_subject TEXT NOT NULL,
      action TEXT NOT NULL,
      detail TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
  `);
  return db;
}
