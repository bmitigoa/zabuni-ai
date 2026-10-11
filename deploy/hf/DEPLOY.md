# Deploying the hosted demo to Hugging Face Spaces (Docker)

What is already done: the `Dockerfile` (Python 3.12 + Node 20 for the Filesystem MCP server), the Streamlit app, and
`deploy/hf/prepare_space.py`, which stages a clean Space repository (code, Space README header, the frozen OCDS snapshot stored with Git LFS).
The container builds the DuckDB from the snapshot at build time (about 20 s on a laptop), so it starts in seconds and never downloads
newer data. **Everything below needs your Hugging Face login; nothing was uploaded for you.**

## 0. Check before you publish
* **Data licence.** The Space repository will contain `data/raw/full.jsonl.gz` (the PPRA Kenya snapshot from the Open Contracting Data
  Registry, publication 147). Open the publication page <https://data.open-contracting.org/en/publication/147>, read its licence, and
  confirm redistribution is allowed with attribution (the Space README already credits the source). If you are not sure, keep the Space *private*
  until you are.
* The Space README says the model is `qwen/qwen3.8-27b` on Groq. Your Groq key's free-tier limits apply to every visitor.

## 1. Account, Space and secret (browser)
1. Sign in at <https://huggingface.co> (create an account if needed; verify your email).
2. **New Space** (<https://huggingface.co/new-space>): name `zabuni-ai`, licence MIT, **SDK: Docker -> Blank**, hardware **CPU basic (free)**, visibility Public.
3. Open the Space -> **Settings -> Variables and secrets**:
   * **New secret** `GROQ_API_KEY` = the key from your local `.env` (copy it from the file; it is never in the repository or the image).
   * **New variable** `DEMO_VIDEO_URL` = the unlisted YouTube link (you can add it on Day 7; until then leave it unset and the "recorded demo" button is hidden).
4. Create an access token with **Write** permission: <https://huggingface.co/settings/tokens> (name it `zabuni-space-push`). You will paste it as the password when pushing, or log in once with the CLI.

## 2. Stage and push (terminal, Windows)
```bash
# in the project folder
.venv312/Scripts/python deploy/hf/prepare_space.py          # creates build/space (about 43 MB, 51 files)
.venv312/Scripts/pip install -U "huggingface_hub[cli]"
.venv312/Scripts/hf auth login                              # paste the write token (older versions: huggingface-cli login)
.venv312/Scripts/hf upload YOUR-USERNAME/zabuni-ai build/space . --repo-type=space --commit-message "Deploy Zabuni AI demo"
```
`hf upload` handles the large snapshot for you (no git-lfs needed). If your `hf` version is older, use `huggingface-cli upload` with the same arguments.

Alternative with git (needs `git lfs install` once):
```bash
cd build/space
git init -b main && git lfs install && git add . && git commit -m "Deploy Zabuni AI demo"
git remote add space https://huggingface.co/spaces/YOUR-USERNAME/zabuni-ai
git push space main        # user name = your HF user, password = the write token
```
The Space repository is only a deployment artefact. The challenge repository stays <https://github.com/bmitigoa/zabuni-ai>; do not push the staged folder there.

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
