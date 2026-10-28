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

## Sign-in (who can use the app)

Two locks, on purpose:

1. **In the app: Google sign-in plus an allowlist** (`app/auth.py`).
   - Visitors sign in with Google (OpenID Connect, Streamlit's `st.login`);
     no passwords exist anywhere.
   - Only verified e-mails on the allowlist, or on an allowed company
     domain, get in.
   - Without the settings below the app **stays closed** (fails closed).
2. **On the platform: Community Cloud private sharing.**
   - In the app's **Settings -> Sharing**, choose **Only specific people can
     view this app** and invite the same people.
   - Streamlit then blocks anyone uninvited before the app even runs.
   - The free tier allows one private app.

The data itself is public: it's committed to this public repo. These locks
protect the app (its analysis and supplier pages), not the raw numbers.

### One-time setup: a Google OAuth client

1. **Open the project.** Go to
   [console.cloud.google.com](https://console.cloud.google.com) and create (or pick)
   a project.
2. **Configure the consent screen** under **APIs & Services -> OAuth consent screen**:
   - **Internal** if everyone signs in with the company's Google Workspace accounts.
     Only that domain can even try; this is the safest choice.
   - Otherwise **External**. While it stays in *Testing*, only the test users you list
     can sign in: a third lock.
   - App name: "Beef export intelligence"; add your support e-mail.
3. **Create the client** under **APIs & Services -> Credentials -> Create credentials ->
   OAuth client ID**:
   - application type **Web application**;
   - **Authorized redirect URIs:**
     - `https://YOUR-APP.streamlit.app/oauth2callback` (the deployed app's URL);
     - `http://localhost:8501/oauth2callback` (only if you'll test sign-in locally).
4. **Copy the client ID and secret.**
5. **Make a cookie secret:**
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

### Add the settings to the deployed app

On share.streamlit.io, open the app's **Settings -> Secrets** and paste the contents of
`.streamlit/secrets.toml.example` with the real values:

- `redirect_uri`: the app's URL + `/oauth2callback`;
- `cookie_secret`: the random string from step 5;
- `client_id`, `client_secret`: from step 4;
- under `[access]`, the people allowed in: exact addresses in `allowed_emails`, whole
  company domains in `allowed_domains`.
  - A domain matches exactly: `sebbeninternational.com.br` admits
    `ana@sebbeninternational.com.br`.
  - It does not admit look-alikes or subdomains.

Save. The app restarts. To add or remove someone later, edit `[access]` there: no code
change, no deploy.

Never commit real values: `.streamlit/secrets.toml` is gitignored, and only the example
with placeholders lives in the repo.

### Running locally

The app is closed without settings, so for local development switch the gate off:

```bash
DV_BEEF_EXPORTS_AUTH=off uv run streamlit run src/dv_beef_exports/app/main.py
```

(PowerShell: `$env:DV_BEEF_EXPORTS_AUTH = "off"` first.) The tests do the same
(`tests/app/conftest.py`). **Never set it on the deployed app.** To test the real
sign-in locally instead, copy the example to `.streamlit/secrets.toml` with real values.

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
