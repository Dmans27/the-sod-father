# The Sod Father

A small, separate project exploring whether Dony's card-based UI (map pins +
expandable cards) works just as well as a golf content/media site, where
each "card" is an article instead of a place. No map, no database, no
payments -- this is a lightweight prototype you can run locally and decide
whether it's worth building out further.

## Running it

```bash
cd golf-content
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5050

## Deploying (Render)

This repo is ready to deploy as-is:

- `Procfile` runs it with gunicorn (`web: gunicorn app:app`)
- `render.yaml` defines a free-tier web service, so Render's "New Blueprint"
  flow picks it up automatically from this repo with no manual config
- `requirements.txt` includes `gunicorn` alongside Flask

## What's here

- `app.py` -- the whole Flask app (3 routes: homepage, category filter, article page)
- `articles.py` -- all the content, as a plain Python list of dicts. No
  database required. Add a new article by adding a new dict here -- the
  templates pick it up automatically.
- `templates/` -- homepage card grid, article detail page, shared layout
- `static/css/style.css` -- the whole visual design (forest green / cream /
  gold, deliberately different from Dony's green/white so the two don't
  look like reskins of each other)

## If you want to take this further

A few natural next steps, roughly in order of effort:

1. **Swap `articles.py` for a real database** (SQLite to start, same
   pattern Dony uses) once you're past "is this worth doing" and into
   "I'm actually going to write articles regularly."
2. **Add real photos** instead of the emoji/gradient placeholders -- the
   card and article hero both already have a spot for an image, they just
   fall back to a colored gradient when there isn't one.
3. **An admin/write flow** so you don't have to edit a Python file to post
   a new article -- a simple form that writes a new row to the database.
4. **Course guides with a real map** -- if you ever want to bring the map
   back in specifically for course-guide articles (course location, yardage
   book, etc.), that's the one category here where Dony's map/pin code
   would genuinely carry over, since courses are real places with real
   coordinates.

None of this needs to happen for the idea to be worth testing -- it runs
as-is with zero setup beyond `pip install`.
