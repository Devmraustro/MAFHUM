# Rollback guide

Purpose: return MAFHUM to the last known-good state after a bad deploy, a failed
migration or data loss. Read this **before** an incident; the commands assume the
pre-deploy backup from `docs/DEPLOYMENT_RUNBOOK.md` §3 exists and was verified.

---

## 1. Decide what kind of rollback you need

| Symptom | Action |
|---|---|
| Bad image, schema unchanged | §3 (image rollback only) |
| Bad migration / schema mismatch | §4 (restore + downgrade) |
| Lost or corrupt blobs | §4 (restore database **and** blobs together) |
| Bad frontend only | Redeploy the previous web image/tag; API untouched |
| Secret leak | Rotate `SESSION_SECRET`, revoke all sessions, then §3 |

A restore of the database without the matching blob snapshot produces rows that
point at nothing. A restore of blobs without the database produces files nobody
can authorize. **Always restore the pair from the same archive.**

---

## 2. Stop the bleeding

```bash
docker compose stop api web
# Keep db running: it is needed for the restore. If the database itself is
# corrupt, stop it too and restore onto a fresh cluster.
docker compose ps -a
```

Do not delete the `storage` or `pgdata` volume yet. Nothing is unrecoverable
until a volume is removed. Capture the failing container logs first:

```bash
docker compose logs --tail=200 api > incident-api.log
docker compose logs --tail=200 web > incident-web.log
```

---

## 3. Image rollback (schema unchanged)

```bash
export PREVIOUS_TAG=<image tag or commit SHA recorded before deploy>
docker compose up -d --no-build api web    # with the previous images pulled/tagged
curl -fsS http://127.0.0.1:8000/health/ready
python tools/e2e/compose_smoke.py
```

If the previous image expects an older schema, this is **not** sufficient — go
to §4.

---

## 4. Full restore from a verified archive

1. Verify the archive before using it:
   ```bash
   python apps/api/tools/backup.py \
     --verify-archive ./backups/mafhum-<stamp>.tar \
     --database-url "postgresql://user:pw@127.0.0.1:5432/mafhum_restore_check" \
     --storage-root ./restore-check-blobs
   ```
   A non-zero exit means the archive is not usable. Stop and escalate.

2. Restore into the live target (destructive — confirm the target host/DB):
   ```bash
   python - <<'PY'
   from pathlib import Path
   from tools.backup import read_manifest, restore_backup, verify_state
   archive = Path("./backups/mafhum-<stamp>.tar")
   target_db = "postgresql://user:pw@127.0.0.1:5432/mafhum"
   target_blobs = Path("/var/lib/mafhum/storage")
   manifest = read_manifest(archive)
   restore_backup(archive=archive, database_url=target_db, storage_root=target_blobs)
   print(verify_state(database_url=target_db, storage_root=target_blobs, manifest=manifest))
   PY
   ```
   `verify_state` must report `ok: true` with zero missing blobs and zero
   checksum mismatches. If it does not, the system is not restored.

3. Align the schema with the code that will run:
   ```bash
   alembic current                # what the restored dump contains
   alembic history --verbose
   # Downgrade one revision at a time; never jump across an untested gap.
   alembic downgrade 0008_presentations
   # ...or downgrade to the revision the previous image expects.
   ```

4. Start the previous images and re-run the smoke journey (§3).

---

## 5. After the rollback

- `GET /api/v1/admin/audit-events` records what happened; the trail is
  append-only and survives account deletion.
- Check `GET /api/v1/documents/limits` — the scanner state may have changed.
- Rotate `SESSION_SECRET` if any secret could have been exposed; this revokes
  every session by making its digest unusable.
- Write down: which archive was used, its SHA-256, the schema revision, the
  image tag, and the exact `verify_state` output. Attach them to the incident.

---

## 6. Known limits of this guide

- No automated failover, no point-in-time recovery and no WAL archiving are
  configured. Backups are operator-triggered snapshots.
- Backups taken while the application is writing are marked
  `quiesced_asserted: false` in the manifest; do not treat them as consistent
  without stopping the writer first.
- Retention of archives, off-site copies and encryption at rest of backups are
  **not implemented** in MAFHUM and must be handled by the operator.
- The restore commands above have been exercised against a scratch database in
  `apps/api/tests/test_backup_restore.py` and in the deployment-rehearsal
  workflow; they have **not** been executed against a real production incident.
