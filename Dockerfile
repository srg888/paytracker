FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc libmagic1 && rm -rf /var/lib/apt/lists/*

# Set environment variable to disable advanced CPU features for numpy
ENV NUMPY_DISABLE_CPU_FEATURES=SSE4_2,POPCNT,SSSE3,SSE4_1,AVX,AVX2,AVX512F,AVX512CD,AVX512_KNL,AVX512_KNM,AVX512_SKX,AVX512_CLX,AVX512_CNL,AVX512_ICL,AVX512_DL,AVX512_VBMI,AVX512_VBMI2,AVX512_VNNI,AVX512_BITALG,AVX512_VPOPCNTDQ

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
