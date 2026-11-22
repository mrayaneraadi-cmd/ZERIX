# ZERIX — Gaming Shorts Studio

A Streamlit app that turns gameplay clips into vertical Shorts with Whisper subtitles and FFmpeg rendering.

## Run locally

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Deploy

Deploy this repository with Streamlit Community Cloud. Set the app entrypoint to `app.py` and select Python 3.12 in Advanced settings.

## Notes

- This app performs CPU-intensive video rendering and speech recognition. Free cloud resources may be limited; use short clips and the `tiny` Whisper model for testing.
- Uploaded videos are processed by the server running the app. Do not upload private or sensitive footage to a public demo.
- Auto Highlights currently detects visual scene changes; it does not semantically identify kills, wins, or other gameplay events.
