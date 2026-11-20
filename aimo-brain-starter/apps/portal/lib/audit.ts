import { getDb } from "./db";

export function recordPortalAction(params: {
  organizationId: string;
  oidcSubject: string;
  action: string;
  detail: Record<string, unknown>;
}): void {
  getDb()
    .prepare(
      `INSERT INTO audit_log (organization_id, oidc_subject, action, detail, created_at)
       VALUES (?, ?, ?, ?, ?)`,
    )
    .run(
      params.organizationId,
      params.oidcSubject,
      params.action,
      JSON.stringify(params.detail),
      new Date().toISOString(),
    );
}

export interface AuditLogRow {
  id: number;
  organizationId: string;
  oidcSubject: string;
  action: string;
  detail: string;
  createdAt: string;
}

export function listPortalActions(organizationId: string): AuditLogRow[] {
  const rows = getDb()
    .prepare(`SELECT * FROM audit_log WHERE organization_id = ? ORDER BY id DESC LIMIT 200`)
    .all(organizationId) as Array<Record<string, string | number>>;
  return rows.map((row) => ({
    id: row.id as number,
    organizationId: row.organization_id as string,
    oidcSubject: row.oidc_subject as string,
    action: row.action as string,
    detail: row.detail as string,
    createdAt: row.created_at as string,
  }));
}
