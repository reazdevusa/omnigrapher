FROM python:3.11

# OpenCV / ONNX Runtime shared libraries + ffmpeg for yt-dlp audio extraction
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 libsm6 libxext6 libxrender1 libfontconfig1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# Generous timeout/retries: CUDA wheels are 500MB+ and PyPI throttles.
RUN pip install --no-cache-dir --default-timeout=300 --retries=20 -r requirements.txt

# aria2: parallel range-request downloader for yt-dlp (googlevideo throttles
# single connections hard). Kept out of the base apt layer so that layer and
# the big pip layer below stay cached.
RUN apt-get update && apt-get install -y --no-install-recommends aria2 \
    && rm -rf /var/lib/apt/lists/*

# CUDA 12 runtime libs for ctranslate2 GPU transcription — kept as a separate
# layer so the big requirements install stays cached. ldconfig puts the
# pip-installed libs on the loader path for subprocesses.
RUN pip install --no-cache-dir --default-timeout=300 --retries=20 "nvidia-cublas-cu12" "nvidia-cudnn-cu12==9.*" \
    && printf '%s\n' \
      /usr/local/lib/python3.11/site-packages/nvidia/cublas/lib \
      /usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib \
      /usr/local/lib/python3.11/site-packages/nvidia/cuda_nvrtc/lib \
      > /etc/ld.so.conf.d/nvidia-pip.conf \
    && ldconfig

COPY . .

# Deno JS runtime for yt-dlp (YouTube player challenge solving)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

# Let ctranslate2 find the pip-installed NVIDIA CUDA libs
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib:${LD_LIBRARY_PATH}

EXPOSE 8001

HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/api/v1/health', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
