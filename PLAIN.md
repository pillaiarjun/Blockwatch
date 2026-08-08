# Block Watch, explained simply

**Live demo:** https://block-watch-1034942560016.us-east1.run.app

## What is this?

New York City has hundreds of traffic cameras pointed at street corners.
Anyone can look at their pictures for free — but almost nobody does, because
a blurry photo of an intersection doesn't tell you much by itself.

Block Watch is a robot that watches one of those cameras for you and tells
you what's happening, in normal words.

## How does it work?

Think of it as three helpers working together:

1. **The Watcher** 👀 — Every 3 seconds, it grabs the newest picture from the
   camera and asks an image-recognition service (Roboflow): "How many people,
   cars, trucks, buses, and bicycles do you see in this picture?" It writes
   the answer down.

2. **The Notebook** 📓 — It keeps a running list of those answers for the
   last several minutes. One picture doesn't mean much, but a list of them
   shows *change*: "there were 5 people, now there are 40."

3. **The Narrator** 🗣️ — Once a minute, it hands the notebook to an AI
   (Google's Gemini) and asks: "Read these numbers and tell me what's going
   on, like a neighbor looking out the window." That's how you get sentences
   like *"heavy pedestrian crowds cleared out — likely a traffic signal cycle
   clearing the crosswalk."*

The webpage shows all of it at once: the live picture, the current counts,
a little bar chart of the last 20-ish minutes, and the narrator's latest
report on an amber highway-sign-style board.

## What's the dropdown at the top?

That's the fun part: you can point Block Watch at **any** of NYC's ~1,000
online traffic cameras, live, from the page itself. Pick a different corner
of the city and the whole thing — picture, counts, chart, narration — starts
fresh on the new block within a few seconds.

## Does it spy on people?

No. It counts *categories* ("8 people, 3 cars"), it can't tell who anyone
is, and it never saves pictures anywhere — each photo is thrown away as soon
as the next one arrives. The camera feeds themselves are public and already
published by the city.

## Why is this cool?

Because the city publishes this data every few seconds and it mostly goes
unread. Block Watch turns a firehose of blurry photos nobody looks at into
one sentence a person would actually read — which is most of what "an AI
agent" means: software that watches something for you and speaks up in your
language.

## The ingredients

- **NYC DOT traffic cameras** — the free public pictures
- **Roboflow** — the "what's in this picture?" counter
- **Google Gemini** — the plain-English narrator
- **Flask + Google Cloud Run** — the small web app that holds it together

Built in one evening at AI Tinkerers NYC "Vision Hack v.2" by Arjun Pillai
and Soham Banerjee. The technical version of this story is in
[README.md](README.md).
