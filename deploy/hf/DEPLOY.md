# Deploying the hosted demo to Hugging Face Spaces (Docker)

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
