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

Stories are written at `/admin` instead of by editing code, with a
block-by-block story builder, photo uploads, and the whole thing is
password-protected.

**The story builder:** instead of picking a fixed layout up front, a story
is just a stack of blocks you add in whatever order the story needs --
paragraph, photo (with a caption), pull quote (with an attribution), a
subheading to break up a longer piece, or a custom embed/code block for
pasting in something like a YouTube or tweet embed. Click "+ Paragraph",
"+ Photo", etc. to add a block, drag it by its `⠿` handle to move it
anywhere, or hit the `×` to remove it. A photo gallery is just several
photo blocks in a row; a ranked list is a subheading + photo + paragraph
repeated -- there's no separate "gallery" or "listicle" mode to pick
anymore, the builder just does both (and anything in between) directly.

A quick caution on the custom embed block: whatever you paste there
renders exactly as-is on the live site, so only paste code from sources
you trust (it's the same trust level as editing a story's JSON file by
hand -- fine since you're the only one with `/admin` access, just don't
paste something you haven't looked at).

**How saving works:** there's no database and Render's free tier has no
persistent disk, so a save doesn't write to a local file that would just
vanish on the next restart. Instead, saving a story commits it (and any
photos) straight to this GitHub repo via the GitHub API -- the same thing a
`git push` does, just triggered from the form instead of a terminal. That
commit triggers the usual Render auto-deploy, so a save takes about 1-2
minutes to actually go live. Run locally without `GITHUB_TOKEN` set, saves
just write to the local `content/`/`static/uploads/` folders instead, so
you can try the portal out without touching the real site.

Stories written before the block builder existed (the old "standard
article" / "photo gallery" / "listicle" types) still work exactly as
before -- they're automatically converted to blocks when read, so they
display and open for editing in the builder the same as anything written
natively in it. Nothing on disk needs to change by hand; the first time
you re-save one of those older stories through `/admin`, it's written back
out in the new block format.

## Authors: bylines with a real photo

Writers don't need an `/admin` login to be credited properly. They sign up
themselves at `/authors/signup` (behind a separate sign-up password, so
random visitors can't add themselves) with a name, photo, and short bio,
then sign in any time at `/authors/login` to update it. In `/admin`, the
Author field on a story is a dropdown of everyone who's signed up -- pick
one and their name and photo come along automatically. There's also a
read-only `/admin/authors` list so you can see who's registered.

Authors can only manage their own profile -- they can't write or edit
stories. You're still the only one doing that in `/admin`. A story can
also be credited to someone with no account at all: pick "Other" in the
dropdown and type a name, same as before -- useful for a one-off guest
byline, or any of the existing articles that predate this feature.

## Required environment variables (set these in Render → the service →
Environment)

- `ADMIN_PASSWORD` -- the password for `/admin`. Pick anything reasonably
  strong; there's only one account.
- `AUTHOR_SIGNUP_PASSWORD` -- a separate password you give to writers you
  want to invite, so they can create an author profile at `/authors/signup`.
  Not required for the site to run -- without it, that page just says
  sign-up isn't set up yet.
- `GITHUB_TOKEN` -- a GitHub token with write access to this repo, so saves
  (stories and author profiles both) can actually be committed. Easiest way
  to get one -- **use a classic token, not fine-grained**: fine-grained
  tokens have a known gap where GitHub rejects the lower-level Git API
  calls this app uses to batch a story's JSON and photos into one commit,
  even with Contents permission set to read/write (`403: Resource not
  accessible by personal access token`). A classic token doesn't have that
  problem:
  1. GitHub → Settings → Developer settings → Personal access tokens →
     **Tokens (classic)** → Generate new token (classic)
  2. Name it something like "the-sod-father admin", set whatever expiration
     you're comfortable with
  3. Scopes → check **`repo`** (that's the only one needed)
  4. Generate it, copy the token, and paste it into Render as `GITHUB_TOKEN`
     — GitHub only shows it once.
- `SECRET_KEY` -- any random string (used to sign login session cookies,
  both `/admin` and `/authors`). Something like the output of `python -c
  "import secrets; print(secrets.token_hex(32))"` is fine.

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
  article page) plus wiring up the admin and author blueprints
- `admin.py` -- the `/admin` portal: login, dashboard, the story form, the
  read-only authors list
- `author_auth.py` -- the `/authors` portal: sign up, sign in, edit profile
- `content.py` -- reads articles from `content/articles/*.json` (categories
  and the block types the story builder offers also live here), and
  converts any pre-block-builder story to the current block format on read
- `authors.py` -- reads author profiles from `content/authors/*.json`
- `content/articles/*.json` -- the actual story content, one file per
  story. Written by the admin portal; fine to hand-edit too if you ever
  need to.
- `content/authors/*.json` -- author profiles (name, email, hashed
  password, photo, bio), one file per author, written by `/authors`
- `story_store.py` -- resizes/compresses uploaded photos and hands a save
  (a story or an author profile) off to `github_content.py` (or writes
  locally in dev mode)
- `github_content.py` -- commits files straight to this GitHub repo via its
  API, no local git required
- `static/uploads/` -- photos stories and author profiles use, organized
  one subfolder per story slug (and `static/uploads/authors/<slug>/` for
  profile photos)
- `templates/` -- homepage card grid, the article page (renders a story's
  blocks in order), shared site layout, `templates/admin/` for the admin
  portal (`story_form.html` is the block builder itself), `templates/authors/`
  for author sign-up/sign-in/profile
- `static/css/style.css` -- the public site's visual design (forest green /
  cream / gold); `static/css/admin.css` -- the admin and author portal look

## If you want to take this further

- **Course guides with a real map** -- if you ever want to bring the map
  back in specifically for course-guide articles (course location, yardage
  book, etc.), that's the one category here where Dony's map/pin code
  would genuinely carry over, since courses are real places with real
  coordinates.
- **Let authors write their own stories** -- right now an author account
  only manages a profile; you're still the one writing every story in
  `/admin`. Giving each author a restricted version of the story form
  (their own stories only) is the natural next step if this grows past a
  one-person operation.
- **More block types** -- the builder currently offers paragraph, photo,
  quote, subheading, and custom embed; adding another (a pull-out stat box,
  a video block, etc.) just means a new entry in `BLOCK_TYPES` in
  `content.py`, a render case in `templates/article.html` and
  `renderSheet()` in `base.html`, and a `<template>` in `story_form.html`.
