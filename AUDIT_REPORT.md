# Property Agent Bot — code audit report

## Scope and test limits

Reviewed the active app, all project-specific Python modules from the uploaded ZIP, README, requirements, database schema and existing image-path records. Python syntax compilation was run against the project files. Streamlit is not installed in this analysis runtime, and the ZIP's `.venv` is a Windows virtual environment, so I could not run the UI server or test the browser rendering against your actual Windows environment.

## File-by-file review summary

| Original path | Review result | Disposition in clean package |
|---|---|---|
| `app.py` | Compiled successfully. Main active app contained its own CSS/database/CRM/recommendation code and an older vertically stacked property card; depended on `services/` for AI and search. | Improved, made self-contained, and retained as the only active app module. |
| `frontend/components.py` | Compiled successfully. It included a two-column card, but the active `app.py` did not import it. | Reviewed; omitted to remove the inactive second UI implementation. |
| `frontend/styles.py` and `frontend/__init__.py` | Compiled successfully. CSS was duplicated in `app.py`; the active app did not import the stylesheet module. | Reviewed; omitted so changes are made in the stylesheet that is actually used (CSS inside `app.py`). |
| `backend/config.py`, `database.py`, `crm.py`, `duplicates.py`, `recommendations.py`, `utils.py`, `__init__.py` | Compiled successfully. These largely duplicate functions already defined in active `app.py`, causing two possible sources of truth. | Reviewed; omitted from the clean package because the functions needed at runtime are already in `app.py`. |
| `services/local_ai.py` | Compiled successfully. Original model and URL were fixed constants. | Reviewed and inlined; the standalone app now supports `OLLAMA_MODEL` and `OLLAMA_GENERATE_URL` environment overrides. |
| `services/property_search.py` | Compiled successfully. The dashboard parser did not consistently treat `budget 20000` as a max-price filter and used stricter comparison patterns. | Reviewed and inlined with broader budget parsing and case-insensitive comparisons. |
| Root `test_local_ai.py` and `services/test_local_ai.py` | Both compiled successfully but were duplicate scripts tied to `services.local_ai`. | Removed from the clean single-file package because the service module is no longer a runtime dependency. |
| `init_db.py` | Compiled successfully; initializes the database and inserts sample listings only when the table is empty. | Retained and adjusted to close its SQLite connection explicitly. |
| `requirements.txt` | Valid dependency file; `requests` was not used by the active app, which uses Python's standard `urllib`. | Retained with the unused `requests` dependency removed. |
| `README.md` | Described a modular layout that did not match the duplicated/embedded logic in `app.py`. | Rewritten for the standalone runtime and Windows setup. |
| `backup/*.py` | Most compiled successfully; two files had `TabError` in an old description block. | Historical copies retained; the two mixed-indentation blocks were normalized. They are not active runtime modules. |
| `assets/properties/*` | All five image files were opened and verified successfully. | Retained; property photos and user-uploaded photo are unchanged. |
| `data/property_agent.db` and `data/property_agent_backup_phase3.db` | Both SQLite integrity checks returned `ok`. | Both retained; main database is byte-for-byte unchanged. |
| `.gitignore` | Reviewed as project metadata. | Retained. |
| `.git/` and `.venv/` | The archive included Git internals and a Windows virtual environment with third-party packages. These are not app source files and are not portable to another machine. | Excluded; recreate the environment from `requirements.txt`. |

**HTML display discrepancy:** The exact property-details HTML snippet reported by the user was not present in the uploaded ZIP's active `app.py`; the uploaded active card function used Streamlit widgets, but placed the image above the details. The separate frontend component had the side-by-side layout but was unused. That means the literal HTML display likely came from a different `app.py` version than the ZIP. The new implementation uses single-line HTML snippets only for escaped badge/metadata elements, always with `unsafe_allow_html=True`, and uses native Streamlit layout for the main card to avoid the multi-line HTML-as-code failure pattern.

## Issues found and addressed

1. **Property cards were not responsive / photo and details were stacked.** In the uploaded `app.py`, `show_property_card()` at the original line 1015 rendered the image first and then the details in the same vertical container. The separate `frontend/components.py` already had a two-column example but is not imported by `app.py`, so editing that module would not change the live app. The updated `app.py` has one active two-column card renderer, with consistent 1100×700 cropped thumbnails, title/price hierarchy, location, listing/type badges, and detail cells.
2. **Duplicate sources of truth.** The uploaded `app.py` defines database, CRM, duplicate, and recommendation functions directly, while similar implementations also existed under `backend/`; it also embedded its own CSS while a separate `frontend/styles.py` existed. The active app did not import those frontend/backend modules. This is a maintainability issue and explains why styling changes in the separate frontend file can appear to have no effect. The clean package keeps the active runtime logic in `app.py` and omits those unused duplicate folders.
3. **Stale absolute image paths.** The supplied SQLite database includes an image path beginning `C:\Users\manus\...` for an uploaded property image, while the file is also present inside `assets/properties/user_uploads/`. Absolute paths break if the project is moved or copied to another Windows account. The updated resolver checks the filename in the project image folders as a fallback.
4. **SQLite connections were not explicitly closed.** The app used `with get_connection() as conn` while `get_connection()` returned a raw `sqlite3.Connection`. Python's native connection context manager commits/rolls back, but does not itself close the connection. The updated SQLite connection wrapper now explicitly commits/rolls back and closes connections used for queries, backup creation, and backup validation.
5. **Database restore temp files could accumulate.** The original restore UI created a `NamedTemporaryFile` on every Streamlit rerun when a backup was selected, and deleted it only if the restore button was pressed. Merely selecting a file could leave files in the OS temp directory. The updated code uses `TemporaryDirectory`, which is cleaned on each run.
6. **Budget search language was limited.** The uploaded parser handled phrases such as `under 20000` but not the example-like `budget 20000` consistently in the dashboard parser. The inline parser now accepts `budget`/`budget of`, currency markers, lakh/crore and `k` units, and does case-insensitive filtering.
7. **Two archival backups contained indentation errors.** `backup/app_before_phase4_replacement.py` and `backup/app_error_backup.py` failed compilation with `TabError` at original line 591 because tabs and spaces were mixed in the same nested `description` block. These historical copies were normalized so the archive's Python files pass syntax compilation. They are not imported by the live app.
8. **The original archive included `.venv/` and `.git/`.** These made the ZIP about 118 MB and included a Windows Python environment and version-control internals. The delivered ZIP excludes both; recreate `.venv` by using `requirements.txt`.

## Database and asset checks

- Main SQLite database: `data/property_agent.db` (preserved byte-for-byte in the updated package).
- Tables present in the uploaded database: `properties`, `enquiries`, `lead_interactions`, `property_ratings`, and SQLite's sequence table.
- At audit time: 4 property rows, 1 enquiry row, 0 interaction rows, 0 rating rows. No record edits were made.
- The database has required property fields plus `image_path` and `amenities`; the app also checks/adds optional fields on startup.
- The bundled property images and uploaded image file were retained.

## Syntax result

The active app and project utility modules in the uploaded archive compiled, but two older backup scripts failed as noted above. After the updates, all Python files retained in the delivered ZIP pass `py_compile`.

## Remaining validation

This is a static/code-level repair. Please still run the app on your Windows machine and test: property cards, upload a listing with a photo, AI search when Ollama is running and when it is stopped, create a backup, and backup restore only with a copy/test database first. I have not claimed a live Streamlit browser test because Streamlit was not installed in the audit runtime.
