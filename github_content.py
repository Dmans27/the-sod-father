"""
Commits files straight to the live GitHub repo using GitHub's REST API --
no local git involved at all. That matters for two reasons: Render's free
web service has no persistent disk (anything saved to the local filesystem
is gone the next time the service restarts or redeploys), and git commands
against Danny's synced Desktop folder are known to crash. Committing via the
API sidesteps both: the content lives in the repo (durable, versioned), and
every push to main is exactly what already triggers Render's auto-deploy.

Multiple files (an article's JSON plus any photos it uses) are batched into
a single commit with the Git Data API (blobs -> tree -> commit -> ref
update), so saving one story is one commit and one Render deploy, not one
per file.
"""
import base64
import os

import requests

REPO = os.environ.get("GITHUB_REPO", "Dmans27/the-sod-father")
BRANCH = os.environ.get("GITHUB_BRANCH", "main")
API_ROOT = f"https://api.github.com/repos/{REPO}"


class GitHubContentError(RuntimeError):
    """Raised when a commit to the content repo can't be completed -- a
    missing/invalid token, a network issue, or the GitHub API rejecting the
    request. Callers show this message to the admin user as-is."""


def _token():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GitHubContentError(
            "GITHUB_TOKEN isn't set, so there's nowhere to save this to. "
            "Set it in Render's environment variables (a GitHub token with "
            "write access to this repo) and redeploy."
        )
    return token


def _headers():
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _request(method, url, **kwargs):
    try:
        resp = requests.request(method, url, headers=_headers(), timeout=20, **kwargs)
    except requests.RequestException as e:
        raise GitHubContentError(f"Couldn't reach GitHub to save this: {e}") from e
    if resp.status_code >= 300:
        detail = resp.json().get("message", resp.text) if resp.content else resp.reason
        raise GitHubContentError(f"GitHub rejected the save ({resp.status_code}): {detail}")
    return resp.json() if resp.content else None


def commit_files(files, message, author_name=None, author_email=None):
    """files: {path_in_repo: bytes_content}. Creates one commit on BRANCH
    containing all of them (adds new paths, overwrites existing ones,
    leaves every other file in the repo untouched) and returns the new
    commit sha."""
    if not files:
        raise GitHubContentError("Nothing to save.")

    ref = _request("GET", f"{API_ROOT}/git/ref/heads/{BRANCH}")
    latest_commit_sha = ref["object"]["sha"]

    latest_commit = _request("GET", f"{API_ROOT}/git/commits/{latest_commit_sha}")
    base_tree_sha = latest_commit["tree"]["sha"]

    tree_entries = []
    for path, content in files.items():
        blob = _request(
            "POST",
            f"{API_ROOT}/git/blobs",
            json={"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"},
        )
        tree_entries.append({
            "path": path,
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"],
        })

    new_tree = _request(
        "POST",
        f"{API_ROOT}/git/trees",
        json={"base_tree": base_tree_sha, "tree": tree_entries},
    )

    commit_payload = {
        "message": message,
        "tree": new_tree["sha"],
        "parents": [latest_commit_sha],
    }
    if author_name and author_email:
        commit_payload["author"] = {"name": author_name, "email": author_email}

    new_commit = _request("POST", f"{API_ROOT}/git/commits", json=commit_payload)

    _request(
        "PATCH",
        f"{API_ROOT}/git/refs/heads/{BRANCH}",
        json={"sha": new_commit["sha"]},
    )

    return new_commit["sha"]
