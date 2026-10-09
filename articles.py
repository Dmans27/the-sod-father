"""
Seed content for the golf media site.

Each article is a plain dict so this whole project can run with zero database
setup -- the "cards are the articles" idea just needs something to list and
something to read, and a Python list does that fine for a fun prototype.
Swap this for a real database (or a markdown-file-per-article setup) later
without touching the templates, since they only ever read these same keys.
"""

CATEGORIES = {
    "equipment":   {"label": "Equipment",     "emoji": "\U0001F3CC️",  "color": "#B8860B"},
    "instruction": {"label": "Instruction",   "emoji": "\U0001F3AF",        "color": "#1D6FA5"},
    "courses":     {"label": "Course Guides", "emoji": "\U0001F3DE️",  "color": "#2D6A4F"},
    "tour":        {"label": "Tour News",     "emoji": "\U0001F3C6",        "color": "#A4303F"},
    "lifestyle":   {"label": "Lifestyle",     "emoji": "⛳",            "color": "#5C6B73"},
}

ARTICLES = [
    {
        "slug": "irons-worth-the-upgrade-this-season",
        "title": "Five Iron Sets Actually Worth the Upgrade This Season",
        "dek": "Forged feel, game-improvement forgiveness, and two sleepers that punch well above their price.",
        "category": "equipment",
        "author": "Sam Whitfield",
        "published_at": "2026-09-28",
        "read_minutes": 6,
        "body": [
            "Iron technology has quietly gone through a bigger shift in the last three years than drivers have, even if drivers get all the headlines. Hollow-body constructions that used to be reserved for tour-level blades have trickled down into sets any mid-handicapper can hit, and the forgiveness gap between “players irons” and “game improvement irons” has narrowed to the point where the old categories barely mean what they used to.",
            "That matters for anyone shopping this season, because it means you're no longer forced to choose between feel and forgiveness the way you were a decade ago. A few sets stand out for actually delivering on that promise rather than just marketing it.",
            "The clearest trend is tungsten weighting pushed low and toward the toe, which does two things at once: it raises launch on mis-hits low on the face, and it straightens out the toe-strike miss that handicap golfers make more often than they'd like to admit. If you've been playing the same set for five-plus years, that alone is worth a fitting session to feel the difference.",
            "The other shift worth knowing about before you buy: multi-material faces are no longer a premium-only feature. A few mid-priced sets now use a thinner, more flexible face insert that used to be reserved for $1,400 sets two generations ago, which means ball speed on center strikes has crept up across the board even in “game improvement” categories.",
            "None of this means last year's irons are suddenly obsolete — if your current set still feels good and the gapping works, there's no rule that says you need new metal every season. But if you've been quietly losing strokes to mis-hits that never used to cost you much, this is a better year than most to go get fit.",
        ],
    },
    {
        "slug": "fix-your-slice-in-one-range-session",
        "title": "The One-Range-Session Fix for a Persistent Slice",
        "dek": "It's almost never your grip. Here's the drill that actually moves the needle.",
        "category": "instruction",
        "author": "Priya Nandakumar",
        "published_at": "2026-10-01",
        "read_minutes": 5,
        "body": [
            "Every golfer with a slice has been told to “strengthen your grip” at some point, usually by a well-meaning friend standing behind them on the range. Sometimes that's genuinely the fix. More often, it's treating the symptom instead of the cause, and the slice comes right back the next time the pressure's on.",
            "The actual root of most amateur slices is an out-to-in swing path combined with an open clubface at impact — and critically, it's the relationship between the two that curves the ball, not either one alone. A strong grip can mask an out-to-in path for a while, but it tends to break down under pressure because it's compensating for the real issue rather than fixing it.",
            "The drill that tends to work in a single session: place an alignment stick or a spare club in the ground just outside your target line, angled away from you, roughly where an out-to-in path would make contact with it on the downswing. Hit half-speed shots with the only goal of missing the stick on the way down. Nothing else — not ball flight, not distance, just missing the stick.",
            "What happens almost immediately is the body finds a path that swings more from the inside, because that's the only way to avoid hitting the stick. You'll feel like you're swinging “out to the right” at first, which is normal and is usually a sign it's working, not a sign something's wrong.",
            "Once that path starts to feel repeatable at half speed, build back up to full swings gradually. The temptation is to rush back to full speed too early, which tends to bring the old pattern back with it. Twenty focused minutes on this, done patiently, moves more amateur slices than a full bucket of swing thoughts ever does.",
        ],
    },
    {
        "slug": "underrated-public-courses-worth-the-drive",
        "title": "Six Underrated Public Courses Worth the Drive",
        "dek": "No member-guest required. Just a tee time and a few hours to spare.",
        "category": "courses",
        "author": "Marcus Hale",
        "published_at": "2026-09-15",
        "read_minutes": 7,
        "body": [
            "The best public golf doesn't always show up on the lists that get shared around, mostly because those lists tend to favor courses with a famous name attached or a wildly scenic back nine that photographs well. Some of the most fun rounds available to the public are quieter than that — a well-routed, well-maintained municipal or daily-fee course that a local pro has been fine-tuning for years without much fanfare.",
            "What tends to separate a genuinely underrated course from one that's just cheap is variety: a good public layout asks a different question on every hole instead of repeating the same shot shape over and over. Width off the tee that still rewards accuracy. Greens with enough movement to matter without turning into miniature golf. A finishing stretch that actually asks something of you instead of coasting in.",
            "Conditioning is the other piece worth checking before you drive an hour for a tee time. A course with modest length and simple bunkering can still be a great round if the greens roll true and the fairways are tight; a flashier-looking course with patchy conditioning rarely is, no matter how good it looks in photos.",
            "If you're building a short list for a weekend trip, it's worth calling ahead and asking the pro shop directly which of their courses the members actually play when they're not showing off for guests — that question tends to surface exactly the kind of course this list is about.",
        ],
    },
    {
        "slug": "weekend-recap-a-crowded-leaderboard",
        "title": "Weekend Recap: A Leaderboard That Stayed Crowded Until the Last Group",
        "dek": "Six players with a share of the lead on the back nine. Only one walked away with it.",
        "category": "tour",
        "author": "Jordan Reyes",
        "published_at": "2026-10-05",
        "read_minutes": 4,
        "body": [
            "Most weeks on tour settle into a rhythm by Sunday afternoon — a front-runner builds a cushion, the chasing pack needs something dramatic to happen, and it usually doesn't. This week went the other way almost from the opening tee shot of the final round.",
            "Six players made a run at the lead at some point on the back nine, and the swings were sharp enough that the top of the leaderboard looked different roughly every twenty minutes. A two-shot lead turned into a tie turned into a share of three turned into a two-shot lead again, all inside about ninety minutes of golf.",
            "What ultimately separated the winner from the rest of the group wasn't a hot putter, which is usually how these things get decided — it was avoiding the one bad swing that cost almost everyone else a shot somewhere on the closing stretch. Scrambling, not scoring, won this one.",
            "It's the kind of finish that's more fun to watch than it is to analyze cleanly afterward, and probably the better outcome for it. Not every tournament needs a tidy storyline; sometimes a crowded leaderboard staying crowded until the last putt drops is the whole story.",
        ],
    },
    {
        "slug": "building-a-golf-trip-your-group-will-actually-finish-planning",
        "title": "How to Plan a Golf Trip Your Group Will Actually Finish Planning",
        "dek": "The group chat always starts strong. Here's how to get it across the finish line.",
        "category": "lifestyle",
        "author": "Theo Marsh",
        "published_at": "2026-09-22",
        "read_minutes": 5,
        "body": [
            "Every golf trip starts the same way: someone drops a course list in the group chat, everyone reacts with a thumbs up, and then nothing happens for six weeks. The idea isn't the hard part. Getting from “we should do this” to an actual tee time on an actual calendar is where most trips quietly die.",
            "The single biggest unlock is picking the date before picking anything else — including the course. A trip with a date but no finalized course still happens. A trip with a dream course but no committed date almost never does, because there's always a reason to wait one more week before locking it in.",
            "Once the date exists, assign one person to own logistics, not by committee. Group trips planned “together” tend to stall on small decisions — which course, what time, who's driving — because nobody wants to be the one who makes the call. One person owning it, with everyone else just confirming, moves noticeably faster.",
            "Budget the conversation early and directly, even though it's the least fun part. A trip that quietly assumes everyone's comfortable with the same green fees and lodging tier is the most common way a group trip turns awkward later. Thirty seconds of directness up front saves a lot of passive-aggressive group chat messages afterward.",
            "None of this is complicated, which is sort of the point — the trips that actually happen aren't the ones with the best course list, they're the ones where somebody just made the decisions instead of waiting for consensus.",
        ],
    },
    {
        "slug": "putter-fitting-is-worth-more-than-you-think",
        "title": "Why a Putter Fitting Moves the Needle More Than You'd Expect",
        "dek": "Half the golfers who get fit are using the wrong length. Most never find out.",
        "category": "equipment",
        "author": "Sam Whitfield",
        "published_at": "2026-10-08",
        "read_minutes": 4,
        "body": [
            "Driver fittings get booked out weeks in advance. Putter fittings, for the club that touches the ball more than any other in the bag, barely get requested at all. That gap doesn't match how much putting actually matters to a scorecard, and it's worth closing.",
            "The two variables that move the most for the average golfer are length and lie angle, and both are commonly off by more than people assume — a putter that's an inch too long for your setup changes your eye position over the ball enough to throw off your read on a straight putt before you've even taken the stroke back.",
            "Face balance versus toe hang is the other piece that actually matters, and it has nothing to do with how expensive the putter is. It's about matching the putter's balance to how much your particular stroke naturally arcs, so the putterhead is square at impact without you having to manipulate it there yourself.",
            "None of this requires an expensive putter. A twenty-minute fitting on a putter you already own, just dialing in length and lie angle, tends to move strokes gained on the greens more reliably than buying a new putter off the rack ever does.",
        ],
    },
]


def get_article(slug):
    for article in ARTICLES:
        if article["slug"] == slug:
            return article
    return None


def get_related(article, limit=3):
    same_category = [
        a for a in ARTICLES
        if a["slug"] != article["slug"] and a["category"] == article["category"]
    ]
    others = [
        a for a in ARTICLES
        if a["slug"] != article["slug"] and a["category"] != article["category"]
    ]
    return (same_category + others)[:limit]
