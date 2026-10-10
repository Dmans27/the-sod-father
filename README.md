# The Sod Father

A small, separate project exploring whether Dony's card-based UI (map pins +
expandable cards) works just as well as a golf content/media site, where
each "card" is an article instead of a place.

## Running it

```bash
cd golf-content
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5050

## Writing stories: the admin portal

Stories are written at `/admin` instead of by editing code. It supports
three story types (standard article, photo gallery, listicle), photo
uploads, and is password-protected.

**How saving works:** there's no database and Render's free tier has no
persistent disk, so a save doesn't write to a local file that would just
vanish on the next restart. Instead, saving a story commits it (and any
photos) straight to this GitHub repo via the GitHub API -- the same thing a
`git push` does, just triggered from the form instead of a terminal. That
commit triggers the usual Render auto-deploy, so a save takes about 1-2
minutes to actually go live. Run locally without `GITHUB_TOKEN` set, saves
just write to the local `content/`/`static/uploads/` folders instead, so
you can try the portal out without touching the real site.

### Required environment variables (set these in Render → the service →
Environment)

- `ADMIN_PASSWORD` -- the password for `/admin`. Pick anything reasonably
  strong; there's only one account.
- `GITHUB_TOKEN` -- a GitHub token with write access to this repo, so saves
  can actually be committed. Easiest way to get one:
  1. GitHub → Settings → Developer settings → Personal access tokens →
     Fine-grained tokens → Generate new token
  2. Resource owner: your account. Repository access: "Only select
     repositories" → `the-sod-father`
  3. Permissions → Repository permissions → **Contents: Read and write**
     (that's the only permission it needs)
  4. Generate it, copy the token, and paste it into Render as `GITHUB_TOKEN`
     — GitHub only shows it once.
- `SECRET_KEY` -- any random string (used to sign the admin login session
  cookie). Something like the output of `python -c "import secrets;
  print(secrets.token_hex(32))"` is fine.

None of these should be typed anywhere except Render's Environment tab --
they're secrets, not something to commit to the repo.

## Deploying (Render)

This repo is ready to deploy as-is:

- `Procfile` runs it with gunicorn (`web: gunicorn app:app`)
- `render.yaml` defines a free-tier web service, so Render's "New Blueprint"
  flow picks it up automatically from this repo with no manual config
- `requirements.txt` includes `gunicorn`, `Pillow` (photo resizing), and
  `requests` (the GitHub API calls) alongside Flask

## What's here

- `app.py` -- the Flask app's public routes (homepage, category filter,
  article page) plus wiring up the admin blueprint
- `admin.py` -- the `/admin` portal: login, dashboard, the story form
- `content.py` -- reads articles from `content/articles/*.json` (categories
  and the list of story types also live here)
- `content/articles/*.json` -- the actual story content, one file per
  story. Written by the admin portal; fine to hand-edit too if you ever
  need to.
- `story_store.py` -- resizes/compresses uploaded photos and hands a save
  off to `github_content.py` (or writes locally in dev mode)
- `github_content.py` -- commits files straight to this GitHub repo via its
  API, no local git required
- `static/uploads/` -- photos stories use, organized one subfolder per
  story slug
- `templates/` -- homepage card grid, the three article layouts (standard /
  gallery / listicle), shared site layout, and `templates/admin/` for the
  portal's own pages
- `static/css/style.css` -- the public site's visual design (forest green /
  cream / gold); `static/css/admin.css` -- the admin portal's look

## If you want to take this further

- **Course guides with a real map** -- if you ever want to bring the map
  back in specifically for course-guide articles (course location, yardage
  book, etc.), that's the one category here where Dony's map/pin code
  would genuinely carry over, since courses are real places with real
  coordinates.
- **More than one writer** -- the single shared password is fine solo; if
  other people start writing, this is the natural point to add real
  accounts.
- **Raise the photo-gallery/listicle cap** -- currently capped at 8
  photos/items per story in the form (`MAX_GALLERY_PHOTOS` /
  `MAX_LISTICLE_ITEMS` in `admin.py`); easy to bump if you need more.
