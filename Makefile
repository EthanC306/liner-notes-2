# Short commands. `make` = start the site at http://localhost:8000
.PHONY: run run-fake-spotify setup demo test

run:
	cd backend && ../.venv/bin/uvicorn main:app --reload

run-fake-spotify:  ## same, but "Connect Spotify" uses sample data instead of a real Spotify login
	cd backend && SPOTIFY_FAKE=1 ../.venv/bin/uvicorn main:app --reload

setup:  ## first time only: install deps and generate demo data
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	$(MAKE) demo

demo:
	.venv/bin/python scripts/make_sample_export.py --seed-db
	.venv/bin/python scripts/make_sample_export.py --friend

test:
	.venv/bin/pytest -q
