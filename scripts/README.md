# Dev & Patch Scripts

These scripts were one-time utilities created during development.
They are **not part of the running application** and are kept here for reference only.

| Script | Purpose |
|---|---|
| `add_appjs_debug.py` | Injected debug logging into app.js |
| `add_cache_bust.py` | Added cache-busting version strings to asset URLs |
| `add_debug.py` | Added on-screen JS error display |
| `add_nocache.py` | Added no-cache headers to Flask static routes |
| `check_js.py` | Syntax-checks app.js for encoding errors |
| `fix_api_url.py` | Rewrote API base URL to relative path |
| `fix_appjs_endings.py` | Fixed CRLF line endings in app.js |
| `fix_apppy.py` | Patched app.py for threaded Flask mode |
| `fix_online_search.py` | Removed erroneous FIELD7=SUR filter from online search |
| `fix_step4.py` | Fixed Step 4 adjoiner discovery bug |
| `patch_deed_viewer.py` | Refactored deed detail viewer HTML generation |
| `patch_step3.py` | Fixed Step 3 plat search logic |
| `patch_threaded.py` | Switched Flask to threaded mode |
| `query_garza.py` | One-off test query for Garza name matching |
| `rebuild_index.py` | Manual trigger to rebuild KML parcel index |
