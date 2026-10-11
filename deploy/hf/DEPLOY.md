# Deploying the hosted demo

Two ways are documented. **Part A, Streamlit Community Cloud, is the one to use (free).** Part B, a Hugging Face Docker Space, is kept as an
alternative: Hugging Face now charges (PRO) for Docker Spaces, so it is not the default.

The OCDS snapshot is in neither repository: it lives in the **private Hugging Face dataset** `bmitigoa/zabuni-ocds-snapshot` (already uploaded and verified:
SHA-256 `15547db9302475fcd5e6da7a0402f5c6f2c84f8baf7d1b7c0b5bf69af8074635` = `data/snapshot.sha256`, 42,529,261 bytes). On first start the app downloads it with a read-only
`HF_TOKEN`, checks the SHA-256, builds the DuckDB once and deletes the raw file.

---

# Part A. Streamlit Community Cloud (free)

## What is already prepared in the repository
* **Entry point** `app/streamlit_app.py`; Python **3.12** (chosen in the deploy dialog, step A3).
* `requirements.txt` (installed by Streamlit Cloud) and **`packages.txt`** (`nodejs`, `npm`, installed with apt) for the official Filesystem MCP server.
  **Node version:** the pinned server depends on `minimatch` 10, which declares **Node 20 or newer** (its other dependencies accept 18). The apt `nodejs` on a Debian-based
  host can be 18, so the app checks the version at start (`agent/node_runtime.py`): it uses the system Node only if it is 20 or newer, otherwise the Node shipped by the
  pip package `nodejs-wheel-binaries` (pinned in `requirements.txt`, Node 24). If neither works, the error message says what to install. I could not run Streamlit Cloud's image
  from here, so which of the two it ends up using is only known after the first run.
* **First start** (`app/bootstrap.py`): the page shows **"Preparing data... (first start only, about a minute)"** while it fetches the snapshot from the private dataset
  (`deploy/fetch_snapshot.py`, SHA-256 verified), builds `data/zabuni.duckdb` in a child process and removes the raw file. The database is cached for the life of the container;
  a container that was put to sleep repeats the preparation when it wakes.
* **Secrets** are read from Streamlit secrets with the environment as fallback (`GROQ_API_KEY`, `HF_TOKEN`, `LLM_PROVIDER`, `MODEL`, optional `DEMO_VIDEO_URL`). `.streamlit/secrets.toml` is in
  `.gitignore`; `.streamlit/secrets.toml.example` shows the shape. Nothing secret is committed.
* The per-visitor run limit (3 live runs per hour) and the "daily quota reached, see the recorded demo video" message are unchanged.
* Measured on the development machine: building the database peaks at about **790 MB** of memory (1.2 GB committed). If Streamlit Cloud restarts the app during the first start with
  an out-of-memory message, tell me: the fix is to ship a prebuilt DuckDB through the private dataset instead of building it.

## Click-by-click (browser; about 10 minutes plus the first build)
**A1. Make sure the code you want is on GitHub.** The repository `bmitigoa/zabuni-ai` must be **public** and `main` must contain `app/streamlit_app.py`, `requirements.txt`,
`packages.txt` and `data/snapshot.sha256` (the latest `Day 5:` commit does).

**A2. Create a read-only Hugging Face token for the app** (you may reuse a token that has Read access to the dataset only):
1. Open <https://huggingface.co/settings/tokens> -> **Create new token** -> **Fine-grained**.
2. Name: `zabuni-streamlit-read`. Under **Repositories permissions** add `bmitigoa/zabuni-ocds-snapshot` and tick **Read** only. Nothing else.
3. **Create token** and copy it once (it starts with `hf_`). Keep it in a password manager; do not paste it anywhere public.

**A3. Deploy on Streamlit Community Cloud.**
1. Go to <https://share.streamlit.io> and click **Continue to sign-in**; choose **Continue with GitHub** and sign in as `bmitigoa`. If asked, authorize Streamlit to read your GitHub repositories
   (you can limit it to `bmitigoa/zabuni-ai`).
2. Click **Create app** (top right) -> **Deploy a public app from GitHub**.
3. Fill in: **Repository** `bmitigoa/zabuni-ai`; **Branch** `main`; **Main file path** `app/streamlit_app.py`; **App URL** pick `zabuni-ai` (or similar; this becomes `https://<name>.streamlit.app`).
4. Click **Advanced settings**:
   * **Python version:** choose **3.12**.
   * **Secrets:** paste exactly this, replacing the two key values (keep the quotes):
     ```toml
     GROQ_API_KEY = "gsk_your_groq_key_from_your_local_.env"
     HF_TOKEN = "hf_the_read_only_token_from_step_A2"
     LLM_PROVIDER = "groq"
     MODEL = "qwen/qwen3.8-27b"
     ```
     (Add `DEMO_VIDEO_URL = "https://..."` later, when the demo video exists.) Your Groq key is the value of `GROQ_API_KEY` in the local `.env` file: open that file yourself and copy it.
   * Click **Save**.
5. Click **Deploy!**. The log panel opens ("Your app is in the oven"). The first deploy installs the Python packages and the apt packages (about 3-6 minutes).
6. When the app opens you will see **"Preparing data..."** for about a minute, then the form. If you see a red error instead, copy its text to me (it never contains a secret).

**A4. Test it in a private window** (this is the check the challenge asks for):
1. Open the app URL in a **private/incognito window** and note the **cold start**: the time from opening the link to the form appearing (a sleeping app shows "This app has gone to sleep"; click **Yes, get this app back up!**; waking takes about a minute because the data is prepared again).
2. Click the first example, **Run investigation**; the progress list should appear in seconds and the risk board in about 15-60 seconds.
3. Review: approve one flag with a reason, clarify another, submit, finish the rest, read the memo, download the Markdown and the PDF.
4. Submit with empty fields: expect "names no buyer, supplier or period". Start four runs within an hour: expect the "limit of live runs per hour" message on the fourth.
5. Send me the app URL, the cold-start time and anything odd (screenshots help).

**A5. Afterwards.** Put the app URL in the README's "Hosted demo" line. To change a secret: app menu (three dots) -> **Settings** -> **Secrets** (the app restarts). To redeploy after a
code change just push to `main` (the app rebuilds). Free apps go to sleep after about 12 hours without visitors; keep the recorded demo video as the fallback. Rotate the Groq key if you ever pasted it somewhere public.

**Local run with the same secrets** (optional): copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, fill in real values (the file is git-ignored), and run
`.\.venv312\Scripts\python.exe -m streamlit run app\streamlit_app.py`.

---

# Part B. Alternative: Hugging Face Docker Space (needs a paid PRO plan)
Everything below is the earlier kit. It is kept because it is tested up to the point where the Hub asks for PRO.

## Hugging Face Docker Space

What is already done: the `Dockerfile` (Python 3.12 + Node 20 for the Filesystem MCP server), the Streamlit app, and
`deploy/hf/prepare_space.py`, which stages a clean Space repository (code and the Space README header only). At build time `deploy/fetch_snapshot.py` downloads the frozen
snapshot from a private dataset, verifies its SHA-256, and the container builds the DuckDB from it (about 20 s), so it starts in seconds and never
fetches newer data. **Everything below needs your Hugging Face login; nothing was uploaded for you.**

## 0. The snapshot is NOT in the Space repository
The PPRA Kenya OCDS snapshot stays in a **private Hugging Face dataset**, `bmitigoa/zabuni-ocds-snapshot`. The Docker build downloads exactly that file with an
`HF_TOKEN` secret and accepts it only if its SHA-256 equals the value recorded in this repository (`data/snapshot.sha256`:
`15547db9302475fcd5e6da7a0402f5c6f2c84f8baf7d1b7c0b5bf69af8074635`, file `full.jsonl.gz`, 42,529,261 bytes). Check that the licence on
<https://data.open-contracting.org/en/publication/147> allows what you do with the data (the built image still serves award records).

## 1. Account, private dataset, tokens, Space (browser + terminal)
1. Sign in at <https://huggingface.co>.
2. **Upload the snapshot to a private dataset.** Open **PowerShell** in the project folder (`C:\Projects\zabuni-ai`). All commands use the project's Python 3.12 environment; `huggingface_hub` is already in `requirements.txt`, so the `hf` program is `.\.venv312\Scripts\hf.exe`.
```powershell
.\.venv312\Scripts\hf.exe auth login                # paste a WRITE token from https://huggingface.co/settings/tokens
.\.venv312\Scripts\hf.exe repos create bmitigoa/zabuni-ocds-snapshot --repo-type dataset --private
.\.venv312\Scripts\hf.exe upload bmitigoa/zabuni-ocds-snapshot data\raw\full.jsonl.gz full.jsonl.gz --repo-type dataset
(Get-FileHash -Algorithm SHA256 data\raw\full.jsonl.gz).Hash.ToLower()    # must print the value above exactly
```
   (`hf repos create` is the command in `huggingface_hub` 2.x; an older `hf repo create` no longer exists. In Git Bash use forward slashes, `.venv312/Scripts/hf.exe`, and `sha256sum data/raw/full.jsonl.gz` instead of `Get-FileHash`. The upload is about 43 MB; if it is interrupted, run the same command again.)
   Optional check (needs the read token from step 3): `$env:HF_TOKEN = "<read token>"; .\.venv312\Scripts\python.exe deploy\fetch_snapshot.py` downloads the private copy to `data\raw\full.jsonl.gz` and prints `Snapshot verified (SHA-256 ...)`. Close the PowerShell window afterwards so the token is not left in the session.
3. **Create a read-only token for the build:** <https://huggingface.co/settings/tokens> -> *Create new token* -> **Fine-grained** -> under Repositories add
   `bmitigoa/zabuni-ocds-snapshot` with **Read** only -> name it `zabuni-space-build`. Copy it once.
4. **New Space** (<https://huggingface.co/new-space>): name `zabuni-ai`, MIT, **SDK: Docker -> Blank**, CPU basic (free), **Public**.
5. Space -> **Settings -> Variables and secrets**: secrets `GROQ_API_KEY` (from your local `.env`) and **`HF_TOKEN`** (the read-only token from step 3; the build reads it as a build-time secret, it is not baked into the image); variable `DEMO_VIDEO_URL` (later).

## 2. Stage and push the code (the Space repo is now tiny, about 0.3 MB)
```powershell
.\.venv312\Scripts\python.exe deploy\hf\prepare_space.py     # build\space: code + data\snapshot.sha256, NO data file
.\.venv312\Scripts\hf.exe upload bmitigoa/zabuni-ai build\space . --repo-type space --commit-message "Deploy Zabuni AI demo"
```
(Use your own Space name if different. You can also create the Space from the terminal instead of the browser: `.\.venv312\Scripts\hf.exe repos create bmitigoa/zabuni-ai --repo-type space --space-sdk docker`; the secrets in step 1.5 still have to be added in the Space settings page.) The challenge repository stays <https://github.com/bmitigoa/zabuni-ai>; do not push the staged folder there.

If the build fails with `Snapshot fetch failed:` the message says which of these it is: no `HF_TOKEN` secret; the token lacks read access to the dataset; the file name is not exactly `full.jsonl.gz`; or **the SHA-256 differs** (then the wrong file was uploaded: re-run the upload command of step 1.2 with `data\raw\full.jsonl.gz` from this machine and check `Get-FileHash` again).

## 3. Wait for the build
Space page -> **Logs**. The image build takes about 5-10 minutes (pip install, npm install of the Filesystem server, loading the snapshot into DuckDB). Status goes
*Building -> Running*. If it fails, copy the last 30 lines of the build log to me.

## 4. Test the live link (private window)
1. Open the Space's "Embed this Space -> Direct URL" (`https://YOUR-USERNAME-zabuni-ai.hf.space`) in a **private/incognito window**.
2. Note the **cold start**: time from opening the link to the form appearing (a sleeping Space wakes in about a minute; a warm one loads in seconds).
3. Click the first example, **Run investigation**; expect the progress list within seconds and the risk board in about 15-60 s on Groq.
4. Review: approve one flag with a reason, **clarify** another, submit; expect the agent's answer, then decide the rest and read the memo; download the Markdown and the PDF.
5. Submit with empty fields: expect the "names no buyer, supplier or period" message. Run four times quickly: expect the "limit of live runs per hour" message on the fourth.
6. Send me: the Space URL, the cold-start time, and anything that looked wrong (screenshots help). I will record it in the Day 5 notes and the README.

## 5. After deploying
* Put the Space URL in the README's *Hosted demo* line (it currently says `link coming after the Space is created`).
* Free CPU Spaces sleep after about 48 hours without visitors and storage is ephemeral: each wake-up starts with an empty `logs/` and no earlier flags. That is expected for a demo.
* To update: re-run `prepare_space.py` and `hf upload` again; the Space rebuilds.
* Rotate the Groq key if you ever pasted it somewhere public.
