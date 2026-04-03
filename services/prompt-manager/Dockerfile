FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install -e "."
COPY src/ src/
EXPOSE 8002
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8002"]
