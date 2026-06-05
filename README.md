# ClassMate

Pick the right professor in 15 seconds.

Enter a course code at UNC Charlotte, UNC Chapel Hill, or NC State — ClassMate returns a ranked professor list with AI-synthesized insights drawn from Reddit and RateMyProfessor.

**Live:** https://aadhyant-b.github.io/classmate/

## What it does

Students type a course code and get a breakdown of each professor teaching that course — workload, grading style, difficulty, and who the class is actually designed for. Data is pulled from Reddit discussions and RateMyProfessor reviews and synthesized using Claude AI.

Currently covers 760 courses across all three schools.

## Tech stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.13, FastAPI |
| AI synthesis | Claude (Anthropic API) |
| Data sources | RateMyProfessor (GraphQL), Reddit (public JSON API) |
| Frontend | Vanilla HTML / CSS / JS |
| Hosting | Render (backend), GitHub Pages (frontend) |

## Built by

Aadhyant Bhatnagar, CS @ NC State — [aadhyantbhatnagar@gmail.com](mailto:aadhyantbhatnagar@gmail.com)
