FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY understand_anypaper ./understand_anypaper
RUN pip install --no-cache-dir .
CMD ["uvicorn", "understand_anypaper.main:app", "--host", "0.0.0.0", "--port", "8000"]
