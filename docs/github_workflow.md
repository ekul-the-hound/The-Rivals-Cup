# GitHub workflow (macOS / zsh)

Target repository: <https://github.com/ekul-the-hound/The-Rivals-Cup>

**Rules that always apply:** never commit `.env`, keys, tokens, credentials or tunnel files; never
force-push; never rewrite history or rebase unless you deliberately choose to; never paste a
password, token or private key into chat, a file or a command line. This repository contains no
trading code, and pushing it does not connect anything to Wall Street Rivals, Trader View or a broker.

## 1. Check the current state (always safe)

```zsh
git rev-parse --is-inside-work-tree     # "true" if initialized; error "not a git repository" if not
git status                              # clean? untracked files? current branch?
git branch --show-current               # e.g. main
git remote -v                           # which remotes exist
```

When this guide was written the project folder was **not** a git repository yet. In that case
initialize it (this does not touch any file):

```zsh
git init -b main
git config user.name "ekul-the-hound"
git config user.email "<the email tied to your GitHub account>"      # local to this repo only
git status                              # expect "On branch main ... No commits yet" and a list of untracked files
```

## 2. Configure the remote

* **No remote** (empty `git remote -v`):

  ```zsh
  git remote add origin https://github.com/ekul-the-hound/The-Rivals-Cup.git
  git remote -v                         # origin ... (fetch) and (push)
  ```
* **`origin` is already that URL:** do nothing.
* **`origin` points somewhere else:** do **not** change it. Add a second remote:

  ```zsh
  git remote add rivals-cup https://github.com/ekul-the-hound/The-Rivals-Cup.git
  ```

  and use `rivals-cup` instead of `origin` in every push command below.

## 3. Authenticate with GitHub (once per Mac; no secrets typed into this repo)

### Option 1: GitHub CLI (simplest, HTTPS)

```zsh
brew install gh
gh auth login          # choose: GitHub.com -> HTTPS -> "Login with a web browser"; copy the one-time code shown, approve in the browser
gh auth status         # expect: "Logged in to github.com account ekul-the-hound"
gh auth setup-git      # lets git use gh's credentials for https://github.com
```

### Option 2: SSH key

```zsh
ssh-keygen -t ed25519 -C "your-github-email"       # press Enter for the default path; set a passphrase
eval "$(ssh-agent -s)"
ssh-add --apple-use-keychain ~/.ssh/id_ed25519
pbcopy < ~/.ssh/id_ed25519.pub                      # copies the PUBLIC key only
```

GitHub → **Settings → SSH and GPG keys → New SSH key** → paste → save. Then:

```zsh
ssh -T git@github.com                               # expect: "Hi ekul-the-hound! You've successfully authenticated..."
git remote set-url origin git@github.com:ekul-the-hound/The-Rivals-Cup.git     # only if origin is already the target repo
```

(Never share `~/.ssh/id_ed25519`; only the `.pub` file is ever pasted anywhere.)

## 4. Pre-commit safety checks (run every time before committing)

```zsh
# a) ignored files really are ignored
git check-ignore -v .env .venv logs .cache supabase/.temp 2>/dev/null     # each should print a rule

# b) no secrets in what will be committed (add first, then inspect the staged set)
git add -A
git diff --cached --name-only | grep -E '(^|/)\.env($|\.)' | grep -v '\.env\.example$' && echo "STOP: env file staged" || echo "ok: no env files staged"
git diff --cached | grep -nE "eyJ[A-Za-z0-9_-]{15,}|sbp_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{16,}|sb_secret_[A-Za-z0-9]{8,}|sb_publishable_[A-Za-z0-9]{8,}|BEGIN (RSA|OPENSSH|PRIVATE)" && echo "STOP: possible secret" || echo "ok: no secret patterns"

# c) project checks
pytest && ruff check . && ruff format --check . && python scripts/check_no_execution.py && python -m scripts.compliance_audit | tail -1
```

Expected: `ok: no env files staged`, `ok: no secret patterns`, and the compliance audit prints
`RESULT: PASS - ...`. If anything says STOP, unstage with `git restore --staged <file>`, remove the
secret from the file (use an environment variable), rotate the key if it was ever committed or
shared, and re-run. The `.env.example` file may contain **placeholders only**.

If a real secret was ever committed, treat it as leaked: rotate it in Supabase / FRED, then ask for
help cleaning history **before** pushing (do not force-push on your own).

## 5. Commit

```zsh
git status                              # review the list one last time
git diff --cached --stat                # summary of what will be committed
git commit -m "docs: add Supabase, provider, MCP, dashboard, and GitHub setup guides"
git log --oneline -1                    # shows the new commit SHA
```

## 6. Push

First push (creates `main` on GitHub):

```zsh
git push -u origin main                 # or: git push -u rivals-cup main
```

Expected: `branch 'main' set up to track 'origin/main'.` and a `main -> main` line. Then open
<https://github.com/ekul-the-hound/The-Rivals-Cup> and confirm the files are there and `.env` is not.

**If the push is rejected** because the GitHub repository already contains commits (for example it
was created with a README): do **not** force. Inspect first:

```zsh
git fetch origin
git log --oneline origin/main           # what is already there?
git diff --stat HEAD origin/main
```

If you want to keep both histories, merge (not rebase):
`git pull --no-rebase origin main --allow-unrelated-histories`, resolve any conflicts, rerun the
section 4 checks, then `git push -u origin main`. If unsure, stop and ask.

Later pushes:

```zsh
git add -A && git status                # review
# ...section 4 checks...
git commit -m "<clear message>"
git push
```

## 7. Cloning on another machine

```zsh
git clone https://github.com/ekul-the-hound/The-Rivals-Cup.git
cd The-Rivals-Cup
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"
cp .env.example .env                    # then fill in your own values; secrets never come from GitHub
```

## 8. Never do

`git push --force`, `git push --force-with-lease`, `git reset --hard` on work you have not saved,
`git rebase` (unless you choose to), `git branch -D`, `git filter-branch`, committing `.env`, or
adding any broker / Wall Street Rivals / Trader View / browser-automation / Telegram / order code.
The compliance audit (`python -m scripts.compliance_audit`) and `scripts/check_no_execution.py`
fail if such code appears.
