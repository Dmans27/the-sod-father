# Gunicorn reads this file by itself (it looks for gunicorn.conf.py in the
# folder it starts from), so the start command stays "gunicorn app:app".
#
# timeout: gunicorn's default stops any request after 30 seconds. Writing a
#   story draft from a long transcript can take longer than that, so allow 2
#   minutes.
# threads: lets the one worker handle several requests at once, so the site
#   keeps answering readers while the AI model is thinking about an Ask
#   question or a draft.
timeout = 120
threads = 4
