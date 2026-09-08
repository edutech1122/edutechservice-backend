# Switching from Render's plain "python" runtime to Docker because the
# MSBTE gazette OCR fallback (app/tools/msbte_result/ocr_fallback.py) needs
# the `tesseract` OCR engine, which is a system binary -- `pip install`
# alone (the old render.yaml buildCommand) has no way to install it.
#
# Kept deliberately close to what the old python-runtime deploy actually
# did (same base Python version, same `pip install -r requirements.txt`,
# same start command) so this is a mechanical platform change, not a
# rewrite -- the two other MSBTE-unrelated tools on this service should
# behave identically before and after.
FROM python:3.11.9-slim

# tesseract-ocr: the OCR engine binary itself (pytesseract in
# requirements.txt is just a wrapper that shells out to this).
# libgl1: PyMuPDF's rendering path pulls in libGL at import time on some
# slim base images even though this service never opens a display --
# without it, `import fitz` can fail at container startup.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
