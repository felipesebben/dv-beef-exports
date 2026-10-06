# Deploying the app

The Streamlit app is deployed on **Streamlit Community Cloud** (free). It
reads the committed `data/processed/comexstat.duckdb` directly, so there is
no separate database to host, and it redeploys automatically whenever `main`
changes - including when a monthly refresh PR is merged.

## First deploy

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with
   the GitHub account that owns this repo.
2. **Create app** -> deploy from GitHub, and fill in:
   - Repository: `felipesebben/dv-beef-exports`
   - Branch: `main`
   - Main file path: `src/dv_beef_exports/app/main.py`
   - Under **Advanced settings**: Python version **3.12** (the project
     requires `>=3.12`).
3. **Deploy.** Dependencies are installed from `uv.lock` at the repo root
   (Community Cloud checks for it first); no `requirements.txt` needed.

The app doesn't rely on the project itself being installed: `main.py` puts
`src/` on the import path, and `DB_PATH` is anchored to the code's location
rather than the working directory. Both verified in a fresh environment
without the package installed, started from another directory.

## Limiting who can open it

In the app's **Settings -> Sharing**, choose **Only specific people can view
this app** and invite viewers by email. They sign in with Google (if the
email is a Google account) or with a single-use link emailed to them.

Worth knowing (Community Cloud docs, checked 2026-10-03):
- The free tier allows **one private app** at a time.
- Invited viewers can also see the app's analytics.
- The data itself is public anyway - it's committed to this public repo -
  so the viewer list controls who can *use* the app, not who can see the
  numbers.

## After a deploy: if a page shows an ImportError, reboot

Community Cloud picks up a merge to `main` without restarting the Python
process. Page files are re-read on every run, but modules they import
(`app/explanations.py`, `app/common.py`, …) can stay cached from before -
so a page that uses something new in a module fails with
`ImportError: cannot import name …` even though the code on `main` is
correct. Seen 2026-10-06 after the opportunity-index merge (#34).

Fix: on share.streamlit.io, open the app's ⋮ menu → **Reboot app**. Do it
after any merge that adds or renames functions in a shared module.

## Notes

- **Cold starts:** Community Cloud puts apps to sleep after a period without
  visitors; the first visit after that takes a moment to wake it.
- **Writes are ephemeral:** the app opens the database read-write (the
  loader upserts `dim_ncm` on connect), which works on Community Cloud, but
  nothing written there persists or flows back to the repo. Data updates
  only come from the refresh workflow's PRs.
